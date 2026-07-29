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

import charge_loop as c


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


TESTS = [test_takt_ueberlebt_wallbox_fehler, test_takt_erholt_sich,
         test_herzschlag_ohne_wallbox, test_watchdog_entscheidung,
         test_bewacht_startet_neu,
         test_reconnect_raeumt_nicht_die_neue_verbindung_weg,
         test_nachtladen_ohne_wechselrichter, test_pv_regelung_ohne_wechselrichter,
         test_betriebsstufe, test_stumme_leitung_wird_gekappt]


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
