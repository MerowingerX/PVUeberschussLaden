#!/usr/bin/env python3
"""Simulation der Regelung gegen vorgegebene PV-Kurven — ohne Hardware.

Aufruf:
    python test_sim.py            alle Szenarien
    python test_sim.py 2 5        nur Szenario 2 und 5
    python test_sim.py --json d.json   Kurven als JSON für die Diagramme

Modell je Takt: das Auto zieht genau das gesetzte Limit (car_amps deckelt es
auf das, was es wirklich annimmt), der Rest des
PV-Überschusses lädt die Hausbatterie (max BATT_MAX_W), eine Lücke deckt die
Batterie bis BATT_MAX_W, was dann noch fehlt, kommt aus dem Netz. Damit gilt
dieselbe Identität wie an der echten Anlage:

    pv_surplus = grid_w + charge_w + battery_w

PV-Kurven sind in Ampere angegeben (1 A = 690 W = 3 × 230 V), also in der
Einheit, in der auch das Ladelimit abgelesen wird. Sie beschreiben den
Überschuss *nach* Hausverbrauch; house_w kommt für die Erzeugungsanzeige
(Register 32080) wieder dazu.

meter steuert, was die Wallbox meldet: "ok" (Momentanleistung), "none" (die
Box kann es nicht) oder "stale" (Meldung eingefroren). Der Regler schätzt
dann aus dem Limit — Tests 13-16 prüfen genau diese Pfade.
"""

import asyncio
import collections
import json
import sys

import charge_loop as c

WPA = c.VOLTAGE * c.PHASES       # 690 W pro Ampere
BATT_MAX_W = 2500                # Lade-/Entladegrenze der Hausbatterie
BATT_CAPACITY_WH = 5000          # nutzbares Fenster der Hausbatterie
DT = 5                           # Takt in Sekunden (= poll_interval_s)


class FakeBox:
    """Wallbox-Ersatz: merkt sich Limit und Transaktionszustand.

    obeys_limit=False bildet die Box nach, die ein Ladeprofil zwar mit
    "Accepted" quittiert, sich aber nicht daran hält (siehe
    docs/issue_limit_to_6A.md). Sie meldet dann weiter ihren eigenen Wert.
    """

    def __init__(self, accept_start=True, obeys_limit=True, stuck_amps=6.0):
        self.accept_start = accept_start
        self.obeys_limit = obeys_limit
        self.stuck_amps = stuck_amps      # was die Box wirklich freigibt
        self.transaction_id = 1
        self.events: list[tuple[float, str]] = []

    @property
    def real_amps(self) -> float:
        """Was tatsächlich fließen darf — nicht, was der Regler glaubt."""
        return c.state.current_limit if self.obeys_limit else self.stuck_amps

    async def set_limit(self, amps: float):
        c.state.current_limit = amps
        c.state.limit_known = True
        self.events.append((Sim.t, f"Limit {amps:.1f} A"))

    async def remote_start(self) -> str:
        # accept=False: Box lehnt ab (z. B. hängt in "Finishing") — dann greift
        # der Backoff in try_start, statt im Regeltakt weiterzufunken
        if not self.accept_start:
            self.events.append((Sim.t, "START abgelehnt"))
            return "Rejected"
        c.state.charging = True
        c.reset_start_backoff()          # entspricht StatusNotification "Charging"
        self.events.append((Sim.t, "START"))
        return "Accepted"

    async def wake_connector(self):
        self.events.append((Sim.t, "ChangeAvailability-Zyklus"))
        self.accept_start = True         # Box lässt sich aufwecken

    async def remote_stop(self):
        c.state.charging = False
        c.reset_charge_meter()
        self.events.append((Sim.t, "STOPP"))


class Sim:
    t = 0.0

    def __init__(self, curve, soc=50.0, mode="minpv", min_amps=6, night=False,
                 batt_charge_max=BATT_MAX_W, meter="ok", house_w=400.0, car_amps=None,
                 accept_start=True, obeys_limit=True, stuck_amps=6.0):
        self.curve = curve            # Funktion: Minute -> verfügbare PV in A
        self.batt_charge_max = batt_charge_max   # 0 = Batterie nimmt nichts auf (voll/gesperrt)
        self.meter = meter            # ok | none (Box meldet nichts) | stale (Messung eingefroren)
        self.house_w = house_w        # Grundlast des Hauses, steckt nicht in curve
        self.car_amps = car_amps      # Funktion Minute -> was das Auto höchstens nimmt
        self.load_w = 0.0             # tatsächliche Ladeleistung, unabhängig von der Meldung
        self.soc = soc
        self.grid_import_wh = 0.0
        self.charged_wh = 0.0
        self.samples: list[tuple[float, float, float, bool, float, float, float]] = []
        self.series: list[dict] = []          # feine Aufzeichnung für Diagramme
        Sim.t = 0.0

        # Regler-Zustand frisch aufsetzen
        s = c.state
        s.pv_hist = collections.deque()
        s.pv_avg_w = None
        s.mode, s.min_amps, s.released = mode, min_amps, True
        s.night_enabled = night
        s.charging, s.charge_w, s.current_limit = False, 0.0, 0
        s.limit_known, s.limit_set_t, s.limit_warned = False, 0.0, False
        s.limit_refresh_s, s.limit_warn_factor = 300, 0.6
        s.charge_w_seen, s.charge_w_src = None, "keine"
        s.charge_energy_wh = s.charge_energy_t = None
        s.charge_w_max_age_s = 30
        s.surplus_since = s.deficit_since = s.minpv_low_since = None
        s.last_adjust = 0.0
        s.boost_used_wh, s.boost_day, s.boost_last_t = 0.0, None, None
        s.soc, s.battery_w, s.grid_w = soc, 0.0, 0.0
        s.poll_interval_s = DT
        s.start_retry_s, s.box_status = 30, "Preparing"
        c.reset_start_backoff()
        self.box = FakeBox(accept_start=accept_start, obeys_limit=obeys_limit,
                           stuck_amps=stuck_amps)
        c.charge_point = self.box

    def step(self):
        s = c.state
        minute = Sim.t / 60
        pv_w = self.curve(minute) * WPA
        # Das Auto nimmt höchstens, was es kann — sonst genau das, was die Box
        # wirklich freigibt (nicht zwingend das, was der Regler gesetzt hat)
        frei = self.box.real_amps
        amps = frei if self.car_amps is None else min(frei, self.car_amps(minute))
        self.load_w = max(0.0, amps) * WPA if s.charging else 0.0
        # Erzeugung = Überschuss + Hauslast. Das Modell kennt keine
        # DC/AC-Wandlung, also steht dieselbe Zahl in beiden Registern.
        s.pv_w = s.pv_dc_w = pv_w + self.house_w
        # Was die Wallbox davon meldet — der Regler sieht nur das hier
        if self.meter == "ok":
            s.charge_w, s.charge_w_seen, s.charge_w_src = self.load_w, Sim.t, "gemessen"
        elif self.meter == "stale":         # letzte Meldung 5 min alt und eingefroren
            s.charge_w, s.charge_w_seen, s.charge_w_src = 0.0, Sim.t - 300, "gemessen"
        else:                               # Box meldet gar keine Ladeleistung
            s.charge_w, s.charge_w_seen, s.charge_w_src = 0.0, None, "keine"

        net = pv_w - self.load_w
        if net >= 0:                                   # Überschuss -> Batterie laden
            batt = min(net, self.batt_charge_max) if self.soc < 100 else 0.0
        else:                                          # Defizit -> Batterie entladen
            batt = -min(-net, BATT_MAX_W) if self.soc > 5 else 0.0
        s.battery_w = batt
        s.grid_w = net - batt                          # positiv = Einspeisung

        self.soc = max(0.0, min(100.0, self.soc + batt * DT / 3600 / BATT_CAPACITY_WH * 100))
        s.soc = self.soc
        if s.grid_w < 0:
            self.grid_import_wh += -s.grid_w * DT / 3600
        self.charged_wh += self.load_w * DT / 3600

    def sample(self):
        s = c.state
        self.samples.append((Sim.t / 60, s.grid_w + self.load_w + (s.battery_w or 0),
                             s.current_limit, s.charging, s.battery_w, s.grid_w, self.soc))

    def record(self):
        s = c.state
        self.series.append({
            "t": round(Sim.t / 60, 1),
            "pv": round((s.grid_w + self.load_w + (s.battery_w or 0)) / WPA, 2),
            "avg": round((s.pv_avg_w or 0) / WPA, 2),
            "load": round(self.load_w / WPA, 2),
            "batt": round(-(s.battery_w or 0) / WPA, 2),     # positiv = Batterie hilft
            "grid": round(-min(0.0, s.grid_w) / WPA, 2),     # positiv = Netzbezug
            "soc": round(self.soc, 1),
        })

    async def run(self, minutes):
        for i in range(int(minutes * 60 / DT)):
            self.step()
            await c.control_step(Sim.t)
            if i % int(300 / DT) == 0:                 # alle 5 min protokollieren
                self.sample()
            if i % int(30 / DT) == 0:                  # alle 30 s für die Diagramme
                self.record()
            Sim.t += DT


RESULTS: list[dict] = []


def report(name, sim: Sim, note=""):
    RESULTS.append({
        "name": name, "note": note, "series": sim.series,
        "events": [{"t": round(t / 60, 1), "text": ev} for t, ev in sim.box.events],
        "charged_kwh": round(sim.charged_wh / 1000, 2),
        "grid_kwh": round(sim.grid_import_wh / 1000, 3),
        "boost_kwh": round(c.state.boost_used_wh / 1000, 2),
        "soc_start": sim.samples[0][6], "soc_end": round(sim.soc, 1),
    })
    if JSON_OUT:
        return
    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")
    if note:
        print(note)
    print("\n  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC")
    for tmin, pv_w, limit, charging, batt, grid, soc in sim.samples:
        if int(tmin) % 15:                             # Tabelle auf 15-min-Raster
            continue
        print(f"  {int(tmin)//60:02d}:{int(tmin)%60:02d}  {pv_w/WPA:5.1f}  {limit:5.1f} A  "
              f"{'⚡' if charging else ' ·'}   {batt:+7.0f} W  {grid:+7.0f} W  {soc:5.1f} %")
    print("\n  Ereignisse:")
    for t, ev in sim.box.events:
        print(f"    {int(t//3600):02d}:{int(t%3600)//60:02d} {ev}")
    print(f"\n  Geladen: {sim.charged_wh/1000:.1f} kWh | Netzbezug: {sim.grid_import_wh/1000:.2f} kWh"
          f" | Boost verbraucht: {c.state.boost_used_wh/1000:.2f} kWh"
          f" | SOC {sim.samples[0][6]:.0f} % -> {sim.soc:.0f} %")


# --- PV-Kurven (Minute -> verfügbare Leistung in A) --------------------------

def sunny(m):
    """Glockenkurve: 0 -> 10 A -> 0 über 8 h, Peak nach 4 h."""
    return max(0.0, 10.0 * (1 - ((m - 240) / 240) ** 2))


def cloudy(m):
    """Anstieg auf 10 A, 1 h halten, dann 7 A mit Wolkenlöchern und Sonnenfenstern."""
    if m < 90:
        return 10.0 * m / 90
    if m < 150:
        return 10.0
    base = 7.0
    phase = (m - 150) % 60
    if phase < 8:                    # 8 min Wolkenloch, unter Schwellwert
        return 3.0
    if phase < 18:                   # 10 min Sonnenfenster
        return 10.0
    return base


def fading(m):
    """Anlauf auf 8 A, dann dauerhaft 3,5 A — unter dem Schwellwert."""
    if m < 45:
        return 8.0 * m / 45
    if m < 75:
        return 8.0
    return 3.5


def just_under(m):
    """Anlauf auf 6,3 A und dort bleiben — knapp unter der minpv-Startschwelle
    (1,10 × 6 A = 6,6 A), aber über dem Mindeststrom selbst."""
    return min(6.3, 6.3 * m / 45)


def flicker(m):
    """Nur kurze Spitzen: 2 min über der Schwelle, 8 min darunter."""
    return 8.0 if m % 10 < 2 else 2.0


def long_dip(m):
    """Anlauf auf 8 A, dann 3 h bei 4 A — Boost muss das Budget aufbrauchen."""
    if m < 30:
        return 8.0 * m / 30
    if m < 60:
        return 8.0
    return 4.0


def dip_storm(m):
    """9 A Grundlinie, alle 20 min ein 6-min-Loch auf 3 A."""
    if m < 30:
        return 9.0 * m / 30
    return 3.0 if (m - 30) % 20 < 6 else 9.0


def wall(m):
    """Voller Anlauf, dann harter Abriss auf 0 (Gewitter)."""
    return 9.0 if m < 90 else 0.0


# --- Szenarien ---------------------------------------------------------------

async def t1():
    sim = Sim(sunny, soc=50, mode="minpv")
    await sim.run(480)
    report("TEST 1 — sonniger Tag, Glockenkurve bis 10 A, minpv 6 A, 8 h", sim,
           "Erwartet: ein Start am Vormittag, Limit folgt der Kurve, ein Stopp am Abend.")


async def t2():
    sim = Sim(cloudy, soc=50, mode="minpv")
    await sim.run(480)
    report("TEST 2 — bewölkt mit Sonnenstunden, 8 h, minpv 6 A", sim,
           "Erwartet: Wolkenlöcher (8 min) werden von Boost + Timeout überbrückt, keine Stopps.")


async def t3():
    sim = Sim(fading, soc=50, mode="minpv")
    await sim.run(240)
    report("TEST 3 — Anlauf auf 8 A, dann dauerhaft 3,5 A, minpv 6 A", sim,
           "Erwartet: Start, dann Boost hält 6 A, bis der minpv-Timeout (10 min) die Ladung stoppt.")


async def t4():
    sim = Sim(fading, soc=50, mode="pv")
    await sim.run(240)
    report("TEST 4 — wie Test 3, aber Modus pv (kein Mindeststrom)", sim,
           "Erwartet: Start, Limit fällt mit dem Mittel, Stopp über stop_delay statt Timeout.")


async def t5():
    sim = Sim(flicker, soc=50, mode="minpv")
    await sim.run(120)
    report("TEST 5 — nur kurze Spitzen (2 min über Schwelle, 8 min darunter)", sim,
           "Erwartet: gar kein Start — start_delay 120 s wird nie durchgehalten.")


async def t6():
    sim = Sim(long_dip, soc=50, mode="minpv")
    await sim.run(300)
    report("TEST 6 — Anlauf auf 8 A, dann 3 h bei 4 A, minpv 6 A", sim,
           "Erwartet: Boost federt den Einbruch ab, das 10-min-Mittel zieht das Ziel aber "
           "unter die Pause-Schwelle -> Timeout -> Stopp, lange bevor das Budget leer ist.")


async def t7():
    # Batterie nimmt nichts auf, damit der SOC unter der Boost-Grenze bleibt
    sim = Sim(fading, soc=25, mode="minpv", batt_charge_max=0)
    await sim.run(240)
    report("TEST 7 — wie Test 3, aber SOC 25 % (unter boost_min_soc 30 %)", sim,
           "Erwartet: kein Boost, Ladung stoppt früher, Batteriereserve bleibt unangetastet.")


async def t8():
    sim = Sim(wall, soc=50, mode="minpv")
    await sim.run(180)
    report("TEST 8 — voller Anlauf, dann harter Abriss auf 0 A", sim,
           "Erwartet: Boost + Timeout federn ab, Stopp danach, Netzbezug begrenzt.")


async def t9():
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        sim = Sim(lambda m: 0.0, soc=50, mode="minpv", night=True)
        await sim.run(120)
        report("TEST 9 — Nachtfenster ohne PV", sim,
               "Erwartet: sofort 16 A aus dem Netz, unabhängig vom Modus.")
    finally:
        c.in_night_window = orig


async def t10():
    sim = Sim(just_under, soc=50, mode="minpv")
    await sim.run(180)
    report("TEST 10 — Dauerhaft 6,3 A bei minpv 6 A (Startschwelle 1,10 × 6 A = 6,6 A)", sim,
           "Erwartet: kein Start — 6,3 A reichen für den minpv-Trigger nicht.")


async def t11():
    sim = Sim(just_under, soc=50, mode="pv")
    await sim.run(180)
    report("TEST 11 — wie Test 10, aber Modus pv", sim,
           "Erwartet: Start bei 6 A — ohne minpv-Trigger genügt das Mittel über 6 A.")


async def t12():
    sim = Sim(dip_storm, soc=90, mode="minpv")
    await sim.run(480)
    report("TEST 12 — Dauerfeuer kurzer Wolkenlöcher (6 min alle 20 min), 8 h", sim,
           "Erwartet: Boost überbrückt jedes Loch, bis das Tagesbudget (5 kWh) leer ist — "
           "danach greift der Timeout.")


async def t13():
    sim = Sim(sunny, soc=50, mode="minpv", meter="none")
    await sim.run(480)
    report("TEST 13 — wie Test 1, aber Wallbox meldet keine Ladeleistung", sim,
           "Erwartet: identisch zu Test 1. Ohne Schätzung aus dem Limit hielte der Regler "
           "die eigene Ladung für einen PV-Einbruch und liefe in einen Stopp-Start-Kreisel.")


async def t14():
    sim = Sim(sunny, soc=50, mode="minpv", meter="stale")
    await sim.run(480)
    report("TEST 14 — wie Test 1, aber Ladeleistung eingefroren (letzte Meldung 5 min alt)", sim,
           "Erwartet: identisch zu Test 1 — die tote Messung wird nach 30 s verworfen.")


async def t15():
    # Box meldet "Charging", das Auto nimmt nichts an (voll oder Ladeabbruch)
    sim = Sim(sunny, soc=50, mode="minpv", meter="none", car_amps=lambda m: 0.0)
    await sim.run(480)
    report("TEST 15 — Wallbox meldet Ladung, Auto nimmt nichts an", sim,
           "Erwartet: Stopp über den minpv-Timeout. Ohne den Deckel aus der PV-Erzeugung "
           "bliebe die Ladung offen, weil die geschätzte Leistung einen Überschuss vortäuscht.")


async def t16():
    # Auto nimmt hartnäckig nur 6 A, das Limit darf ihm nicht davonlaufen
    sim = Sim(sunny, soc=50, mode="minpv", meter="none", car_amps=lambda m: 6.0)
    await sim.run(480)
    report("TEST 16 — Auto nimmt nur 6 A, Wallbox meldet keine Ladeleistung", sim,
           "Erwartet: Limit bleibt in der Nähe dessen, was das Auto zieht, statt bis 16 A "
           "hochzulaufen — der Deckel aus PV minus Netz minus Batterie hält es fest.")


async def t17():
    # Box hängt in "Finishing" und lehnt den Start ab, bis sie geweckt wird
    sim = Sim(sunny, soc=50, mode="minpv", accept_start=False)
    c.state.box_status = "Finishing"
    await sim.run(480)
    report("TEST 17 — Box lehnt Start ab (hängt in Finishing)", sim,
           "Erwartet: ein abgelehnter Versuch, dann ChangeAvailability-Zyklus, danach Start. "
           "Ohne Backoff liefe der RemoteStart im 5-Sekunden-Takt weiter.")


async def t18():
    # Fahrzeug voll: Box meldet SuspendedEV und nimmt keinen Start an
    sim = Sim(sunny, soc=50, mode="minpv", accept_start=False)
    c.state.box_status = "SuspendedEV"
    await sim.run(480)
    versuche = sum(1 for _, e in sim.box.events if "abgelehnt" in e)
    report("TEST 18 — Fahrzeug voll (SuspendedEV), Box nimmt keinen Start an", sim,
           f"Erwartet: wenige Startversuche über 8 h statt Dauerfunk — hier {versuche}. "
           "Ohne Bremse wären es rund 4000 (alle 5 s).")


async def t19():
    """Regression zu docs/issue_limit_to_6A.md.

    Der Regler hat im PV-Betrieb zuletzt 6 A gesetzt und glaubt weiter daran.
    Dann bootet die Wallbox — was in ihr steht, weiß danach niemand mehr — und
    das Nachtfenster beginnt. Vor dem Fix unterblieb das Setzen, weil der
    Merker "6 A" gegen MAX_AMPS verglichen wurde und die Ladung dann tatsächlich
    mit 6 A lief.
    """
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        sim = Sim(lambda m: 0.0, soc=50, mode="minpv", night=True,
                  obeys_limit=False, stuck_amps=6.0)
        # Ausgangslage: 6 A aus dem PV-Betrieb, Box gebootet
        c.state.current_limit, c.state.limit_known = 6.0, False
        await sim.run(120)
        gesetzt = [e for _, e in sim.box.events if e.startswith("Limit")]
        report("TEST 19 — Nachtfenster nach Box-Boot, Merker steht auf 6 A", sim,
               f"Erwartet: sofort {c.MAX_AMPS} A, obwohl der Merker 6 A sagte. "
               f"Gesetzte Limits: {gesetzt[:3]}")
    finally:
        c.in_night_window = orig


async def t20():
    """Box quittiert das Limit, hält sich aber nicht daran.

    Dagegen hilft kein Nachsetzen — der Regler soll es wenigstens bemerken und
    in regelmäßigem Abstand erneut versuchen, statt es einmal zu setzen und den
    Rest der Nacht darauf zu vertrauen.
    """
    orig = c.in_night_window
    c.in_night_window = lambda *a: True
    try:
        sim = Sim(lambda m: 0.0, soc=50, mode="minpv", night=True,
                  obeys_limit=False, stuck_amps=6.0)
        await sim.run(60)
        wiederholt = sum(1 for _, e in sim.box.events if e.startswith("Limit"))
        report("TEST 20 — Box ignoriert das Ladeprofil, 1 h Nachtfenster", sim,
               f"Erwartet: Limit wird über die Stunde mehrfach nachgesetzt "
               f"(hier {wiederholt}×, Abstand {c.state.limit_refresh_s} s) und die "
               f"Diskrepanz einmal geloggt. Vor dem Fix blieb es bei einem Versuch.")
    finally:
        c.in_night_window = orig


TESTS = [t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15, t16, t17, t18,
         t19, t20]


JSON_OUT = ""


async def main():
    global JSON_OUT
    c.log.disabled = True                              # Regler-Logs aus, Tabelle zählt
    c.state.avg_window_s = 600
    c.state.boost_w, c.state.boost_wh, c.state.boost_min_soc = 2500, 5000, 30.0
    args = sys.argv[1:]
    if "--json" in args:
        i = args.index("--json")
        JSON_OUT = args[i + 1]
        args = args[:i] + args[i + 2:]
    wanted = [int(a) for a in args] or range(1, len(TESTS) + 1)
    for n in wanted:
        await TESTS[n - 1]()
    if JSON_OUT:
        with open(JSON_OUT, "w") as fh:
            json.dump(RESULTS, fh)
        print(f"{len(RESULTS)} Szenarien -> {JSON_OUT}")


if __name__ == "__main__":
    asyncio.run(main())
