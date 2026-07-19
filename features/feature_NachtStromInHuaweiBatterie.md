# Batterie günstig laden

Wenn der Nachstrom aktiv ist und die Batterie der Solaranlage unter 30% liegt, soll Sie über netz geladen werden, bis 80 %, und nur, wenn nächsten tag keine Sonne erwartet wird, 

Zum testen will ich das Netzladen der Batterie aus dem Webfenster aktivieren können, ohne Nachtfenster

## Plan

### Parameter (final)

Alle Parameter über .env (Konvention: `PVUEB_*`-Prefix, Loader existiert),
Defaults im Code, Eintrag in `.env.example`:

| .env-Variable | Default | Bedeutung |
|---|---|---|
| `PVUEB_BATT_LOW_SOC` | 30 | Netzladung starten unter diesem SOC (%) |
| `PVUEB_BATT_TARGET_SOC` | 80 | Netzladung stoppen ab diesem SOC (%) |
| `PVUEB_BATT_CHARGE_W` | 500 | Ladeleistung der Zwangsladung (W), nach Test erhöhen |
| `PVUEB_FORECAST_MIN_KWH` | 5 | „keine Sonne“ = Prognose morgen unter diesem Wert (kWh) |
| `PVUEB_LAT` | 52.27 | Standort Breitengrad |
| `PVUEB_LON` | 10.52 | Standort Längengrad |
| `PVUEB_PV_KWP` | 7 | Anlagenleistung (kWp) |
| `PVUEB_PV_TILT` | 42 | Modulneigung (°) |
| `PVUEB_PV_AZIMUT` | 0 | Ausrichtung (0 = Süd, forecast.solar-Konvention) |

### M1 — Register verifizieren (nur lesen) ✅ 2026-07-16

Am Gerät gelesen (`poc/read_battery_registers.py`), Mapping bestätigt gegen
`huawei_solar` 3.0.6 (PyPI-Quelle). Alle Werte plausibel, Zwangsladung im
Ruhezustand (Befehl = Stop):

| Register | Bedeutung | Typ | Gelesen |
|---|---|---|---|
| 47087 | Charge from grid (Funktion an/aus) | bool | 1 = aktiv ✓ |
| 47088 | Grid charge cutoff SOC | ×0,1 % | 66 % (!) |
| 47100 | Forcible charge/discharge: Befehl (0 = Stop, 1 = Laden, 2 = Entladen) | enum | 0 = Stop ✓ |
| 47101 | Forcible charge/discharge: Ziel-SOC | ×0,1 % | 20 % (Altwert) |
| 47246 | Forcible: Setting-Modus (0 = Dauer, 1 = Ziel-SOC) | enum | 1 = SOC ✓ |
| 47247 | Forcible charge power | W (u32) | 116 W (Altwert) |
| 47249 | Forcible discharge power | W (u32) | 0 ✓ |
| 47083 | Forced charging/discharging period | min | 0 ✓ |
| 47086 | Working mode (C) | enum | 2 = Maximise self consumption ✓ |
| 37760 | Batterie-SOC (Gegenprobe) | ×0,1 % | 90 % ✓ |

Erkenntnisse:

- **Schreibreihenfolge Start (Ziel-SOC-Modus)**, wie HA-Integration `forcible_charge_soc`:
  1. 47247 ← Leistung (W), 2. 47101 ← Ziel-SOC ×10, 3. 47246 ← 1 (SOC), 4. 47100 ← 1 (Laden).
- **Stop:** 47100 ← 0, dann Aufräumen (47249 ← 0, 47083 ← 0, 47246 ← 0).
- **Achtung:** 47088 (Grid charge cutoff) steht auf 66 % — betrifft die normale
  „Charge from grid“-Funktion. Ob er die Zwangsladung auf Ziel-SOC 80 % deckelt,
  in M2 beim Test beobachten; ggf. auf ≥ 80 % anheben.
- 47099 existiert nicht (Exception), Dauer-Register ist 47083.

### M2 — Netzladung schalten + Web-Toggle (Testmodus) ✅ implementiert 2026-07-16

- `start_battery_grid_charge()` / `stop_battery_grid_charge()` in `charge_loop.py`:
  Schreibsequenz Leistung (47247) → Ziel-SOC (47101) → Modus SOC (47246) →
  Befehl Laden (47100). Stop = Befehl 0.
- Sicherheitsnetz: immer Ziel-SOC-Modus — Wechselrichter stoppt am Ziel selbst,
  auch wenn Skript abstürzt. Polling liest 47100 zurück und erkennt Selbst-Stopp.
- Ein Modbus-Client + asyncio-Lock für Polling und Writes (SDongle-Limit).
- Web-UI: Abschnitt „Batterie“ mit Toggle (Leistung/Ziel aus .env im Buttontext),
  Statuszeilen „Batterie“ (lädt/entlädt W aus Register 37001) und
  „Batterie-Netzladung“ (auch extern gestartete Zwangsladung sichtbar).
- **Livetest bestanden 2026-07-16 ≈ 20:40** (SOC 85 %, Ziel 95 % aus .env):
  - Start: Batterie drehte binnen ~15 s von Entladen 386 W auf **Laden ~356 W**,
    Netz von ±0 auf **672 W Bezug** (= Hausverbrauch + Ladung, Rechnung passt).
  - Ist-Ladung ~356 W bei Sollwert 500 W — vermutlich DC-seitig nach Verlusten;
    fürs Feature egal, ggf. `PVUEB_BATT_CHARGE_W` entsprechend höher wählen.
  - **47088 (Grid charge cutoff, stand 66 %) deckelt die Zwangsladung NICHT** —
    lud bei SOC 85 % problemlos. Frage geklärt, kein Registereingriff nötig.
    (Frank hat den Cutoff in FusionSolar inzwischen auf 95 % gestellt.)
  - Stop: sofort zurück auf Entladen, Befehl 47100 = 0, `battery_grid_charge` False.
  - Nebenbefund behoben: SDongle warf Transaction-ID-Fehler (träge + parallele
    Pi-Instanz). Fixes: Timeout 10 s, 0,3 s Pause zwischen Modbus-Requests,
    Client-Snapshot gegen Reconnect-Race in den Schreibfunktionen.
    Fürs Testen/Entwickeln: Pi-Container stoppen, nur ein Client pro Dongle.

### M3 — Sonnenprognose (forecast.solar) ✅ implementiert 2026-07-16

- API: `https://api.forecast.solar/estimate/{lat}/{lon}/{tilt}/{azimut}/{kwp}`
  (kostenlos, ohne Key), alle Anlagendaten aus .env.
- Abruf-Design (statt „1× bei Fensterbeginn“): eigener Task, alle 6 h,
  Retry nach 15 min bei Fehler — deckt jeden Fensterbeginn ab, weit unter
  API-Limit (12/h). Beide Tage aus `watt_hours_day` gespeichert.
- `relevant_forecast()`: vor 12 Uhr zählt heute, danach morgen — damit stimmt
  die Bezugsnacht auch bei Fenstern über Mitternacht (22:00→06:00).
- Fehlerfall: Prognose bleibt leer → Automatik (M4) lädt dann nicht (konservativ).
- Web-UI: Zeile „Prognose morgen/heute: ☀️/🌥 X,X kWh“, „–“ ohne Daten.
- Getestet gegen echte API: 16.07. 32,2 kWh, 17.07. 17,7 kWh; Statuszeile ok.

### M4 — Automatik ✅ implementiert 2026-07-16

`battery_night_check()` startet echte Netzladung, wenn alle Bedingungen erfüllt:

1. Nachtfenster aktiv (`.env`, gilt auch für den Nachtladen-Toggle im UI)
2. SOC < `PVUEB_BATT_LOW_SOC` (30 %)
3. Prognose der nächsten Tageslichtperiode < `PVUEB_FORECAST_MIN_KWH` (5 kWh);
   keine Prognose (API-Fehler) = kein Start (konservativ)

Verhalten:

- Höchstens **ein Automatik-Start pro Nacht** (Latch) — verhindert auch
  Restart-Schleife, wenn man die Automatik-Ladung manuell stoppt.
- Stop am Ziel-SOC macht der Wechselrichter selbst; am Fensterende stoppen
  wir nur automatisch gestartete Ladungen — der manuelle Web-Toggle (M2)
  bleibt davon unberührt.
- UI zeigt „⚡ AKTIV (Automatik)“ vs. manuell.
- Logik mit 9 Fällen getestet (Stub-Modbus): Start/Latch, jede fehlende
  Bedingung blockiert, Fensterende stoppt nur Automatik, kein Auto-Restart.

Feature komplett. Offen nur Praxisbeobachtung: erster echter Automatik-Lauf
in einer trüben Nacht (inkl. Selbst-Stopp am Ziel-SOC).