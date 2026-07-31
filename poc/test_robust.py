#!/usr/bin/env python3
"""Regressionstests zu docs/issue_nightly_load_did_not_work.md.

Aufruf:
    python test_robust.py

test_sim.py prüft, ob der Regler *richtig* regelt. Hier geht es um die Frage
davor: ob er überhaupt noch regelt. Am 28.07.2026 riss ein abgerissener
OCPP-Aufruf den Regeltakt aus dem asyncio.gather in main(); Web-UI, Modbus und
OCPP-Server liefen weiter, und 17 Stunden lang fiel niemandem auf, dass die
Nachtladung nicht mehr anspringen konnte. Jede Prüfung hier hätte genau einen
Baustein dieses Ausfalls gefangen.
"""

import asyncio
import types

import websockets

import charge_loop as c
import fake_box


FEHLER: list[str] = []


def pruefe(bedingung, text):
    print(f"  {'ok  ' if bedingung else 'FEHL'}  {text}")
    if not bedingung:
        FEHLER.append(text)


class TotBox:
    """Wallbox, deren Verbindung abgerissen ist — jeder Aufruf wirft.

    Genau dieser Fall stand am 28.07.2026 im Log: erst TimeoutError nach 30 s
    Warten auf SetChargingProfile, dann ConnectionClosed.
    """

    def __init__(self, fehler=None):
        self.fehler = fehler or ConnectionResetError("Wallbox-Verbindung getrennt")
        self.versuche = 0

    async def set_limit(self, amps):
        self.versuche += 1
        raise self.fehler

    async def remote_start(self):
        self.versuche += 1
        raise self.fehler

    async def remote_stop(self):
        self.versuche += 1
        raise self.fehler


def grundzustand():
    s = c.state
    s.poll_interval_s = 0.01
    s.released, s.mode, s.min_amps = True, "fast", 6
    s.night_enabled = False
    s.charging, s.current_limit, s.limit_known = False, 0.0, False
    s.grid_w, s.battery_w, s.soc, s.pv_w = 0.0, 0.0, 50.0, 0.0
    s.last_tick, s.tick_error = 0.0, None
    s.watchdog_s = c.WATCHDOG_TIMEOUT_S
    s.start_retry_s = 30
    # Der Betriebsfall dieser Tests ist ein steckendes Fahrzeug. Ohne das
    # startet der Regler seit dem 30.07.2026 gar nicht erst (try_start).
    s.box_status = "Preparing"
    s.leerstart_gemeldet = False
    c.reset_start_backoff()


async def takt_laufen_lassen(runden=5):
    """control_task ein paar Takte laufen lassen und wieder abräumen."""
    task = asyncio.create_task(c.control_task())
    await asyncio.sleep(c.state.poll_interval_s * runden + 0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return task


async def test_takt_ueberlebt_wallbox_fehler():
    """Der Kern: eine werfende Wallbox darf den Regeltakt nicht beenden."""
    grundzustand()
    box = TotBox()
    c.charge_point = box
    task = await takt_laufen_lassen()
    pruefe(task.cancelled(), "Regeltakt lief noch, als der Test ihn abbrach")
    pruefe(box.versuche >= 2, f"mehrfach weiterversucht ({box.versuche} Aufrufe)")
    pruefe(c.state.tick_error is not None, f"Fehler steht in der UI: {c.state.tick_error}")


async def test_takt_erholt_sich():
    """Nach einem Fehler muss der nächste Takt wieder wirken.

    Sonst wäre der Schirm nur eine leisere Art zu sterben: Der Takt läuft,
    aber der Regler bliebe in einem Fehlerzustand hängen.
    """
    grundzustand()
    box = TotBox()
    c.charge_point = box
    await takt_laufen_lassen(3)
    pruefe(c.state.tick_error is not None, "erst Fehler")

    class HeileBox:
        def __init__(self):
            self.limits = []

        async def set_limit(self, amps):
            self.limits.append(amps)
            c.state.current_limit, c.state.limit_known = amps, True

        async def remote_start(self):
            c.state.charging = True
            return "Accepted"

    heil = HeileBox()
    c.charge_point = heil
    await takt_laufen_lassen(3)
    pruefe(c.state.tick_error is None, "Fehler ist nach dem nächsten guten Takt weg")
    pruefe(heil.limits and heil.limits[0] == c.MAX_AMPS,
           f"Modus fast setzte wieder {c.MAX_AMPS} A: {heil.limits[:2]}")


async def test_herzschlag_ohne_wallbox():
    """Getrennte Box ist ein Betriebszustand, kein Reglerfehler.

    Der Herzschlag muss trotzdem schlagen — sonst würde der Wächter jedes Mal
    neu starten, wenn jemand das Ladekabel abzieht.
    """
    grundzustand()
    c.charge_point = None
    vorher = c.state.last_tick
    await takt_laufen_lassen(5)
    pruefe(c.state.last_tick > vorher, "Herzschlag läuft auch ohne Wallbox")
    pruefe(c.state.tick_error is None, "und meldet dabei keinen Fehler")


async def test_watchdog_entscheidung():
    """Die Bedingung des Wächters, ohne den Prozess wirklich zu beenden."""
    grundzustand()
    jetzt = 10_000.0
    c.state.last_tick = jetzt
    pruefe(not c.takt_tot(jetzt + c.state.watchdog_s - 1), "frischer Takt: kein Neustart")
    pruefe(c.takt_tot(jetzt + c.state.watchdog_s + 1), "überfälliger Takt: Neustart")
    c.state.last_tick = 0.0
    pruefe(not c.takt_tot(jetzt), "vor dem ersten Takt: kein Neustart")


async def test_bewacht_startet_neu():
    """Eine abgestürzte Nebenaufgabe läuft neu an, statt alles mitzureißen."""
    orig = c.TASK_RESTART_S
    c.TASK_RESTART_S = 0.01
    laeufe = []
    try:
        async def kaputt():
            laeufe.append(1)
            raise RuntimeError("Cloud weg")

        task = asyncio.create_task(c.bewacht("Test", kaputt))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        c.TASK_RESTART_S = orig
    pruefe(len(laeufe) >= 3, f"mehrfach neu angelaufen ({len(laeufe)}×)")


async def test_reconnect_raeumt_nicht_die_neue_verbindung_weg():
    """Der zweite Fehler: die alte Verbindung löschte die neue.

    Die Pulsar öffnet die neue Verbindung, bevor die alte zumacht. Vorher
    setzte der Handler der alten im finally pauschal charge_point = None —
    danach lief der Regeltakt blind, obwohl die Box verbunden war.
    """
    orig_cp, orig_klasse = c.charge_point, c.ChargePoint
    enden = {}

    class FakeChargePoint:
        def __init__(self, cp_id, websocket):
            self.id, self.ws = cp_id, websocket
            enden[websocket.name] = asyncio.Event()

        async def start(self):
            await enden[self.ws.name].wait()

    def fake_ws(name):
        return types.SimpleNamespace(
            name=name, request=types.SimpleNamespace(path="/1", headers={}))

    c.ChargePoint = FakeChargePoint
    c.state.ocpp_user = ""
    try:
        alt = asyncio.create_task(c.on_connect(fake_ws("alt")))
        await asyncio.sleep(0.02)
        cp_alt = c.charge_point
        neu = asyncio.create_task(c.on_connect(fake_ws("neu")))
        await asyncio.sleep(0.02)
        cp_neu = c.charge_point
        pruefe(cp_neu is not cp_alt, "neue Verbindung hat übernommen")

        enden["alt"].set()                       # alte Verbindung macht jetzt zu
        await asyncio.wait_for(alt, 1)
        pruefe(c.charge_point is cp_neu,
               "die neue Verbindung steht noch, nachdem die alte aufgeräumt hat")

        enden["neu"].set()                       # und zum Schluss auch die neue
        await asyncio.wait_for(neu, 1)
        pruefe(c.charge_point is None, "danach ist niemand mehr verbunden")
    finally:
        c.ChargePoint, c.charge_point = orig_klasse, orig_cp


async def test_nachtladen_ohne_wechselrichter():
    """Rangfolge: Nachtladen braucht die Wallbox, nicht den Wechselrichter.

    Bis 29.07.2026 stand `state.grid_w is None` als Abbruchbedingung vor dem
    ganzen Regeltakt. Ein Modbus-Abbruch legte damit auch das Nachtladen still,
    obwohl der Nachtzweig die Netzleistung gar nicht anfasst.
    """
    grundzustand()
    c.state.mode = "minpv"           # ausdrücklich nicht "fast"
    c.state.night_enabled = True
    c.state.grid_w = None            # Modbus weg
    gestartet = []

    class Box:
        async def set_limit(self, amps):
            c.state.current_limit, c.state.limit_known = amps, True

        async def remote_start(self):
            gestartet.append(c.state.current_limit)
            c.state.charging = True
            return "Accepted"

    c.charge_point = Box()
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        await takt_laufen_lassen(4)
    finally:
        c.in_night_window = orig
    pruefe(gestartet and gestartet[0] == c.MAX_AMPS,
           f"Nachtladung startet ohne Modbus mit {c.MAX_AMPS} A: {gestartet[:1]}")
    pruefe(c.state.tick_error is None, "und das ganz ohne Fehler")


async def test_pv_regelung_ohne_wechselrichter():
    """Umgekehrt: PV-Überschussladen darf ohne Messung nichts anfangen.

    Ohne Netzleistung ist der Überschuss nicht null, sondern unbekannt. Ein
    Start auf Verdacht wäre Netzbezug.
    """
    grundzustand()
    c.state.mode, c.state.night_enabled = "minpv", False
    c.state.grid_w = None
    gestartet = []

    class Box:
        async def set_limit(self, amps):
            c.state.current_limit, c.state.limit_known = amps, True

        async def remote_start(self):
            gestartet.append(amps := c.state.current_limit)
            return "Accepted"

    c.charge_point = Box()
    await takt_laufen_lassen(4)
    pruefe(not gestartet, "kein Start ohne Messung")
    pruefe(c.state.tick_error is None, "und trotzdem kein Fehler im Takt")


async def test_betriebsstufe():
    """Die Rangfolge muss ablesbar sein, nicht nur wirksam."""
    grundzustand()
    c.charge_point = None
    pruefe(c.betriebsstufe()[0] == "kein Laden",
           f"ohne Wallbox: {c.betriebsstufe()}")
    c.charge_point = TotBox()
    c.state.grid_w = None
    pruefe(c.betriebsstufe()[0] == "eingeschränkt",
           f"ohne Wechselrichter: {c.betriebsstufe()}")
    c.state.grid_w = 0.0
    pruefe(c.betriebsstufe()[0] == "voll", f"mit beidem: {c.betriebsstufe()}")


async def test_stumme_leitung_wird_gekappt():
    """Halbtote OCPP-Verbindung: TCP steht, die Box schweigt.

    Ohne diese Wache bleibt `charge_point` gesetzt, jeder Aufruf läuft 30 s in
    den OCPP-Timeout, und der Wächter über dem Regeltakt startet den Prozess
    im Zweiminutentakt neu.
    """
    grundzustand()
    c.state.heartbeat_s = 10
    geschlossen = []

    class StummeBox:
        def __init__(self):
            self.ws = types.SimpleNamespace(close=self._close)

        async def _close(self):
            geschlossen.append(True)

    c.charge_point = StummeBox()
    jetzt = 1_000_000.0

    c.state.last_box_seen = jetzt                      # gerade erst gemeldet
    pruefe(not c.leitung_tot(jetzt), "frische Meldung: Leitung bleibt")
    c.state.last_box_seen = jetzt - 3 * c.state.heartbeat_s - 1
    pruefe(not c.leitung_tot(jetzt),
           f"knapp über 3× Heartbeat, aber unter der Untergrenze "
           f"({c.BOX_SILENCE_MIN_S} s): Leitung bleibt")
    c.state.last_box_seen = jetzt - c.BOX_SILENCE_MIN_S - 1
    pruefe(c.leitung_tot(jetzt), "länger stumm als die Untergrenze: Leitung ist tot")

    orig = c.BOX_LINK_CHECK_S
    c.BOX_LINK_CHECK_S = 0.01
    try:
        c.state.last_box_seen = datetime_jetzt() - 600
        task = asyncio.create_task(c.box_link_task())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        c.BOX_LINK_CHECK_S = orig
    pruefe(geschlossen, "stumme Verbindung wurde geschlossen")
    pruefe(c.charge_point is None, "und freigegeben, damit der Takt nicht hineinredet")


def datetime_jetzt() -> float:
    import datetime
    return datetime.datetime.now().timestamp()


# --- Protokollebene: echter WebSocket gegen unseren eigenen OCPP-Server ------
#
# Alles oberhalb hängt Attrappen direkt an c.charge_point. Damit ist die
# Regellogik geprüft, die Naht zum Gerät aber nicht — und dort ist der Ausfall
# entstanden. Ab hier läuft jede Nachricht durch Serialisierung, WebSocket und
# die echte Zuordnung der ocpp-Bibliothek. Die Eigenheiten der Box stehen in
# fake_box.py, jede mit ihrem Beweisstatus.

OCPP_PORT = 9911


async def mit_server(port: int):
    """Unseren echten OCPP-Server starten, wie main() es tut."""
    return await websockets.serve(c.on_connect, "127.0.0.1", port,
                                  subprotocols=["ocpp1.6"])


async def bis(bedingung, grenze=3.0, takt=0.02):
    """Auf ein Ereignis warten, statt eine Wartezeit zu raten."""
    ende = asyncio.get_event_loop().time() + grenze
    while asyncio.get_event_loop().time() < ende:
        if bedingung():
            return True
        await asyncio.sleep(takt)
    return bedingung()


async def test_protokoll_anmeldung():
    """Die echte OCPP-Anmeldung, nicht ein nachgebautes Objekt.

    Prüft die Kette, die bisher kein Test berührt hat: on_connect, die
    Zuordnung eingehender Nachrichten und das, was die Box von uns
    zurückbekommt.
    """
    grundzustand()
    server = await mit_server(OCPP_PORT)
    try:
        box, ws, task = await fake_box.verbinde(OCPP_PORT)
        await box.anmelden()
        pruefe(c.charge_point is not None, "Server hat die Verbindung übernommen")
        pruefe(c.state.box_status == "Available",
               f"StatusNotification kam an: {c.state.box_status}")
        pruefe(not c.state.limit_known,
               "BootNotification hat den Limit-Merker entwertet (issue_limit_to_6A)")
        await bis(lambda: "HeartbeatInterval" in box.konfiguration)
        pruefe(box.konfiguration.get("HeartbeatInterval") == str(c.state.heartbeat_s),
               f"Box wurde konfiguriert: {box.konfiguration}")
        task.cancel()
        await ws.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_protokoll_nachtladung():
    """Nachtladen über die echte Leitung: Profil raus, Start, Ladung läuft."""
    grundzustand()
    c.state.mode, c.state.night_enabled = "minpv", True
    server = await mit_server(OCPP_PORT)
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        box, ws, task = await fake_box.verbinde(OCPP_PORT)
        await box.anmelden()
        # Das Auto steckt: die Box steht in "Preparing", sonst startet
        # der Regler nicht (leere Dose, siehe try_start).
        await box.melde_status("Preparing")
        takt = asyncio.create_task(c.control_task())
        await bis(lambda: box.laeuft)
        takt.cancel()
        try:
            await takt
        except asyncio.CancelledError:
            pass
        pruefe(box.laeuft, "Ladung läuft")
        pruefe(box.echte_a() == c.MAX_AMPS,
               f"und zwar mit {c.MAX_AMPS} A, nicht weniger — echte {box.echte_a()} A")
        pruefe(c.state.charging, "der Regler weiß es auch (StatusNotification Charging)")
        task.cancel()
        await ws.close()
    finally:
        c.in_night_window = orig
        server.close()
        await server.wait_closed()


async def test_protokoll_box_ignoriert_profil():
    """Belegte Eigenheit: Accepted quittiert, trotzdem 6 A (issue_limit_to_6A).

    Der Regler kann eine solche Box nicht zwingen — er soll es aber merken und
    weiter nachsetzen, statt sich auf ein einmal gesendetes Profil zu verlassen.
    """
    grundzustand()
    c.state.mode, c.state.night_enabled = "minpv", True
    c.state.limit_refresh_s = 0.1          # Auffrischung im Test beschleunigen
    c.state.charge_w_max_age_s = 300
    server = await mit_server(OCPP_PORT)
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        box, ws, task = await fake_box.verbinde(
            OCPP_PORT, haelt_limit=False, eigen_a=6.0)
        await box.anmelden()
        # Das Auto steckt: die Box steht in "Preparing", sonst startet
        # der Regler nicht (leere Dose, siehe try_start).
        await box.melde_status("Preparing")
        takt = asyncio.create_task(c.control_task())
        await bis(lambda: box.laeuft)
        for _ in range(6):                  # ein paar Takte mit Messwerten
            await box.messwerte()
            await asyncio.sleep(0.05)
        await bis(lambda: sum(1 for e, _ in box.ereignisse
                              if e == "SetChargingProfile") >= 3)
        takt.cancel()
        try:
            await takt
        except asyncio.CancelledError:
            pass
        gesetzt = [w for e, w in box.ereignisse if e == "SetChargingProfile"]
        pruefe(all(a == c.MAX_AMPS for a, _ in gesetzt),
               f"Regler setzt durchgehend {c.MAX_AMPS} A: {[a for a, _ in gesetzt][:4]}")
        pruefe(len(gesetzt) >= 3,
               f"und frischt auf, statt einmal zu senden ({len(gesetzt)}×)")
        pruefe(box.echte_a() == 6.0, "die Box lädt trotzdem mit ihren 6 A")
        pruefe(c.state.charge_w_src == "gemessen",
               f"Ladeleistung kam echt über MeterValues: {c.state.charge_w_src}, "
               f"{c.state.charge_w:.0f} W")
        pruefe(c.state.limit_warned,
               "und der Regler hat die Diskrepanz gemeldet statt ihr zu vertrauen")
        task.cancel()
        await ws.close()
    finally:
        c.in_night_window = orig
        c.state.limit_refresh_s = 300
        server.close()
        await server.wait_closed()


async def test_protokoll_reconnect_race():
    """Der Reconnect-Race durch die echte Mechanik, nicht gegen ein Fake-Objekt.

    Die Pulsar öffnet die neue Verbindung, bevor die alte zumacht — am
    28.07.2026 fünfmal in zwei Minuten. Vorher löschte der Handler der alten
    Verbindung beim Aufräumen die neue.
    """
    grundzustand()
    server = await mit_server(OCPP_PORT)
    try:
        box_a, ws_a, task_a = await fake_box.verbinde(OCPP_PORT)
        await box_a.anmelden()
        cp_a = c.charge_point

        box_b, ws_b, task_b = await fake_box.verbinde(OCPP_PORT)
        await box_b.anmelden()
        await bis(lambda: c.charge_point is not cp_a)
        cp_b = c.charge_point
        pruefe(cp_b is not cp_a, "zweite Verbindung hat übernommen")

        task_a.cancel()
        await ws_a.close()                  # die alte macht jetzt zu
        await asyncio.sleep(0.2)
        pruefe(c.charge_point is cp_b,
               "die neue Verbindung steht noch, nachdem die alte weg ist")

        # und sie ist auch wirklich benutzbar, nicht nur gesetzt
        if c.charge_point is not None:
            await c.charge_point.set_limit(c.MAX_AMPS)
        pruefe(box_b.profil_a == c.MAX_AMPS,
               f"über die überlebende Verbindung geht ein Limit raus: {box_b.profil_a}")
        task_b.cancel()
        await ws_b.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_protokoll_start_abgelehnt():
    """Belegte Eigenheit: erster RemoteStart Accepted, der nächste Rejected.

    Am 28.07. zwölfmal so im Log. Ohne Backoff liefe der Start alle 5 s weiter.
    """
    grundzustand()
    c.state.mode, c.state.night_enabled = "minpv", True
    c.state.start_retry_s = 0.05
    server = await mit_server(OCPP_PORT)
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        box, ws, task = await fake_box.verbinde(
            OCPP_PORT, start_akzeptiert_dann_abgelehnt=True)
        await box.anmelden()
        # Box bleibt in Preparing: der Start wird angenommen, führt aber zu nichts
        await box.melde_status("Preparing")
        takt = asyncio.create_task(c.control_task())
        await asyncio.sleep(1.0)
        takt.cancel()
        try:
            await takt
        except asyncio.CancelledError:
            pass
        pruefe(box.starts < 15,
               f"Startversuche gebremst statt Dauerfunk: {box.starts} in 1 s "
               f"(ohne Bremse wären es ~100)")
        pruefe(c.state.tick_error is None,
               "und ein abgelehnter Start ist kein Reglerfehler")
        task.cancel()
        await ws.close()
    finally:
        c.in_night_window = orig
        server.close()
        await server.wait_closed()


async def test_protokoll_leere_dose():
    """Belegte Eigenheit: RemoteStart in eine leere Dose erzeugt Preparing.

    Vorfall in der Nacht zum 30.07.2026: kein Auto in der Einfahrt, trotzdem
    im Verlauf alle 130 s "Fahrzeug angesteckt" und "abgesteckt". Der Regler
    schickte im Nachtfenster RemoteStart, die Box quittierte mit Accepted und
    ging in Preparing — dort wartet sie ConnectionTimeOut lang (Werk: 120 s)
    auf ein Kabel und fällt zurück auf Available. Damit erzeugte der Regler
    genau den Wechsel, den er gleich darauf als Fahrzeug meldete.
    """
    grundzustand()
    c.state.mode, c.state.night_enabled = "minpv", True
    c.state.start_retry_s = 0.05
    server = await mit_server(OCPP_PORT)
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        box, ws, task = await fake_box.verbinde(OCPP_PORT)
        await box.anmelden()                 # meldet Available: die Dose ist frei
        takt = asyncio.create_task(c.control_task())
        await asyncio.sleep(0.5)
        pruefe(box.starts == 0,
               f"kein Startversuch in eine leere Dose ({box.starts}×)")
        pruefe(not box.laeuft, "und folglich keine Ladung")

        # Jetzt steckt jemand an — ab hier darf der Regler wieder.
        await box.melde_status("Preparing")
        await bis(lambda: box.laeuft)
        takt.cancel()
        try:
            await takt
        except asyncio.CancelledError:
            pass
        pruefe(box.laeuft, "nach dem Anstecken läuft die Ladung")
        pruefe(c.state.tick_error is None, "und der Regeltakt blieb heil")
        task.cancel()
        await ws.close()
    finally:
        c.in_night_window = orig
        server.close()
        await server.wait_closed()


async def test_protokoll_hypothese_zeitfenster():
    """HYPOTHESE, nicht belegt: Box befolgt ihren App-Zeitplan auch unter OCPP.

    Trifft das zu, wäre ein Zeitplan als Rückfallebene für den toten Pi teuer
    erkauft — er brächte tagsüber das PV-Überschussladen zum Erliegen. Dieser
    Test behauptet nichts über die Box. Er hält fest, was PVueb täte, *wenn*
    sie sich so verhielte, damit der Fall beim Messen wiedererkannt wird.
    Messung: features/feature_WallboxRueckfallebene.md
    """
    grundzustand()
    c.state.mode, c.state.night_enabled = "fast", False
    c.state.start_retry_s = 0.05
    server = await mit_server(OCPP_PORT)
    try:
        # Zeitplan 00-08 Uhr, aber es ist Tag: die Box lehnt jeden Start ab
        box, ws, task = await fake_box.verbinde(OCPP_PORT, zeitfenster=(0, 8))
        box.zeitfenster = (0, 0)            # Fenster garantiert zu
        await box.anmelden()
        # Das Auto steckt: die Box steht in "Preparing", sonst startet
        # der Regler nicht (leere Dose, siehe try_start).
        await box.melde_status("Preparing")
        takt = asyncio.create_task(c.control_task())
        await asyncio.sleep(0.6)
        takt.cancel()
        try:
            await takt
        except asyncio.CancelledError:
            pass
        pruefe(not box.laeuft, "keine Ladung — die Box lässt uns nicht")
        pruefe(box.starts >= 1, f"PVueb hat es versucht ({box.starts}×)")
        pruefe(c.state.tick_error is None,
               "und behandelt die Ablehnung als Betriebszustand, nicht als Fehler")
        task.cancel()
        await ws.close()
    finally:
        server.close()
        await server.wait_closed()


TESTS = [test_takt_ueberlebt_wallbox_fehler, test_takt_erholt_sich,
         test_herzschlag_ohne_wallbox, test_watchdog_entscheidung,
         test_bewacht_startet_neu,
         test_reconnect_raeumt_nicht_die_neue_verbindung_weg,
         test_nachtladen_ohne_wechselrichter, test_pv_regelung_ohne_wechselrichter,
         test_betriebsstufe, test_stumme_leitung_wird_gekappt,
         # Protokollebene: echter WebSocket, echte ocpp-Bibliothek
         test_protokoll_anmeldung, test_protokoll_nachtladung,
         test_protokoll_box_ignoriert_profil, test_protokoll_reconnect_race,
         test_protokoll_start_abgelehnt, test_protokoll_leere_dose,
         test_protokoll_hypothese_zeitfenster]


async def main():
    c.log.disabled = True          # die erwarteten Fehler-Tracebacks nicht mitdrucken
    for test in TESTS:
        print(f"\n{test.__name__}\n  {(test.__doc__ or '').splitlines()[0]}")
        await test()
    print()
    if FEHLER:
        print(f"{len(FEHLER)} Prüfung(en) fehlgeschlagen:")
        for f in FEHLER:
            print(f"  - {f}")
        raise SystemExit(1)
    print(f"Alle {len(TESTS)} Testfälle bestanden.")


if __name__ == "__main__":
    asyncio.run(main())
