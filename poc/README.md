# M1 – Mess-PoC

Liest Netzleistung, PV-Leistung, Batterieleistung und SOC vom Sun2000 per Modbus TCP.

## Vorbereitung (einmalig, am Gerät)

1. **Power Sensor prüfen:** FusionSolar-App → zeigt sie Netzbezug/Einspeisung live an?
   Wenn ja, ist der DTSU666-H verbaut und Register 37113 liefert Werte.
2. **Modbus TCP freischalten:** FusionSolar-App → Ich → Inbetriebnahme des Geräts
   → mit dem Dongle-WLAN verbinden (Installateurszugang) → Einstellungen
   → Kommunikationskonfiguration → Dongle-Parametereinstellungen
   → Modbus TCP → „Aktivieren (uneingeschränkt)".
3. **IP des Dongles notieren:** im Router nachsehen (Hostname beginnt meist mit
   `EL-` oder `Dongle`), am besten feste IP vergeben.

## Ausführen

```bash
cd poc
python -m venv .venv && .venv/bin/pip install pymodbus
.venv/bin/python read_sun2000.py <inverter-ip>
```

Bei Fehlern `--unit 0` oder `--unit 100` probieren (abhängig von Dongle-Firmware).

## Erfolgskriterien (Stand 2026-07-15)

- [x] Alle vier Werte plausibel gegenüber FusionSolar-App (PV 424 W ↔ App 0,46 kW; Netz 116 W ↔ App 0,12 kW)
- [x] Vorzeichen geklärt: 37113 positiv = Einspeisung, 37001 positiv = Batterie lädt
- [x] Läuft 10 Minuten stabil: 119 Polls à 5 s, 0 Fehler, 0 Verbindungsabbrüche

Gerät: `<inverter-ip>`:502, Unit-ID 1 (echte Werte: LOCAL.md, gitignored).
Achtung: pymodbus ≥ 3.14 nötig (`device_id`-API).

# M2 – OCPP-PoC

Minimaler OCPP-1.6J-Server (`ocpp_server.py`), gegen simulierte Wallbox end-to-end getestet
(Boot, Status, Authorize, Start/StopTransaction, MeterValues, SetChargingProfile, RemoteStart/Stop).

## Wallbox anbinden

1. Server starten:
   ```bash
   .venv/bin/python -u ocpp_server.py
   ```
2. In der Wallbox-App (Bluetooth-Verbindung zur Box nötig): Einstellungen → OCPP →
   aktivieren und URL eintragen: `ws://<rechner-ip>:9000/`.
   Charge Point Identity frei wählbar, z. B. `pulsar`.
3. Wallbox startet neu und verbindet sich; Server loggt `BootNotification`.

## Testkommandos (stdin)

| Kommando | Wirkung |
|---|---|
| `limit 8` | Ladestrom auf 8 A begrenzen |
| `start` / `stop` | Ladevorgang fernstarten/-stoppen |
| `trigger` | MeterValues anfordern |
| `quit` | Server beenden |

## Erfolgskriterien

- [x] Pulsar Pro verbindet sich und bootet gegen den Server (2026-07-15; ufw-Freigabe Port 9000 nötig)
- [x] SetChargingProfile wird mit "Accepted" quittiert
- [ ] `limit 6` / `limit 16` ändert nachweislich die Ladeleistung (MeterValues oder FusionSolar-Verbrauch) — **braucht eingestecktes Auto**
- [ ] `start`/`stop` funktioniert mit eingestecktem Auto
- [ ] MeterValues enthalten Power.Active.Import (sonst per ChangeConfiguration `MeterValuesSampledData` setzen)

Hinweis: App-Slider der Wallbox auf 16 A stellen — er deckelt sonst zusätzlich zum OCPP-Limit.

# M3 – Regel-Loop (`charge_loop.py`)

Verbindet alles: Modbus-Messung → Regel-Logik → OCPP-Steuerung. Dazu Web-Interface.

```bash
# vorher ocpp_server.py beenden (quit) — gleicher Port!
.venv/bin/python -u charge_loop.py
```

- **Web-UI:** `http://<rechner-ip>:8080/` (Handy: ufw-Freigabe nötig:
  `sudo ufw allow from <lan-subnetz> to any port 8080 proto tcp`)
  Zeigt Netz/Überschuss/SOC/Limit/Status, Freigabe-Button, Modus-Buttons.
- **Freigabe:** Laden nur nach Knopfdruck (Web-UI oder stdin `frei`/`sperr`). Start: gesperrt.
- **Modi:** `pv` (nur Überschuss), `minpv` (immer ≥ 6 A), `fast` (16 A).
- **Nachttarif 00–08 Uhr:** freigegebenes Fahrzeug lädt mit 16 A, unabhängig vom Modus.
  Batterie-Netzladung (SOC < 20 % → 80 %) vorerst nur Log-Meldung — Schreibregister unverifiziert.
- **Hysterese:** Start nach 2 min stabilem Überschuss (> ~4,2 kW), Stopp nach 3 min Defizit,
  Limit-Anpassung höchstens alle 25 s.

## Erfolgskriterien

- [ ] Box verbindet sich mit charge_loop, Web-UI zeigt live Werte
- [ ] Freigabe + Modus `fast`: Auto lädt; Freigabe weg: Ladung stoppt
- [ ] Sonniger Tag, Modus `pv`: Ladung folgt dem Überschuss
