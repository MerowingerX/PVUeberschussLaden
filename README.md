# PVueb – PV-Überschussladen

Maßgeschneiderte App für Solar-Überschussladen auf dem Raspberry Pi: liest die
PV-Anlage (HUAWEI Sun2000) per Modbus TCP aus und steuert die Wallbox
(Wallbox Pulsar Pro) lokal per OCPP 1.6J — ohne Cloud, ohne Abo, ohne Fremd-Backend.

Bewusst minimal: Der Code kennt genau diese drei Geräte und einen Haushalt.
Keine Geräteabstraktionen, keine Konfigurierbarkeit auf Vorrat (siehe [PLAN.md](PLAN.md)).

## Funktionen

- **Freigabe per Knopfdruck** — ohne manuelle Freigabe lädt nichts, auch nachts nicht
- **Lademodi:**
  - *Reines PV-Überschussladen* — lädt nur, wenn der Überschuss reicht (≥ ~4,2 kW bei 3-phasig/16 A)
  - *PV + Minimum* — lädt immer mit einstellbarem Mindeststrom (Slider 6–16 A), Überschuss oben drauf
  - *Sofort laden* — volle Leistung, PV egal
- **Nachttarif-Automatik** (abschaltbar): im Tariffenster (fest im Code, Standard 00:00–08:00)
  lädt ein freigegebenes Fahrzeug mit voller Leistung; Hausbatterie-Nachladung bei
  SOC < 20 % ist vorbereitet (inaktiv, bis Schreibregister am Gerät verifiziert sind)
- **Web-UI** fürs Handy: Live-Status (Netz, Überschuss, Ladeleistung, Batterie-SOC,
  Box-Status, Heartbeats), Freigabe-, Modus- und Automatik-Buttons
- Batterie-Priorität ohne Extra-Logik: als Überschuss zählt nur die Einspeisung —
  der Wechselrichter füllt den Hausspeicher von selbst zuerst

## Hardware-Voraussetzungen

| Gerät | Anforderung |
|---|---|
| HUAWEI Sun2000 | SDongle mit Modbus TCP „uneingeschränkt" freigeschaltet |
| Smart Power Sensor (DTSU666-H) | am Netzanschlusspunkt (bei Speicher-Anlagen Standard) |
| Wallbox Pulsar Pro | OCPP aktiviert, URL zeigt auf diesen Server; **App-eigene Zeitpläne löschen**, App-Slider auf 16 A |
| Server | Raspberry Pi oder beliebiger Linux-Rechner im selben LAN |

## Konfiguration

```bash
cp .env.example .env        # PVUEB_INVERTER_IP eintragen (IP des SDongle)
```

Alle anlagenspezifischen Werte (IPs, OCPP-URL) bleiben in `.env` bzw. `LOCAL.md` —
beide sind gitignored. Feste Parameter (bewusst nur im Code änderbar, `poc/charge_loop.py`):

| Parameter | Standard | Bedeutung |
|---|---|---|
| `PHASES`, `VOLTAGE` | 3, 230 V | Anschluss der Wallbox |
| `MIN_AMPS`, `MAX_AMPS` | 6, 16 A | Regelbereich (IEC 61851 / 11-kW-Box) |
| `NIGHT_START_MIN`, `NIGHT_END_MIN` | 00:00–08:00 | Nachttarif-Fenster (Tarif-Parameter) |
| `START_DELAY_S` / `STOP_DELAY_S` | 120 / 180 s | Hysterese gegen Wolken-Flattern |
| `ADJUST_MIN_INTERVAL_S` | 25 s | Mindestabstand zwischen Limit-Änderungen |
| `BATTERY_LOW_SOC` / `BATTERY_TARGET_SOC` | 20 / 80 % | Nachtladung Hausbatterie |

### Einrichtung

```bash
cd poc
python -m venv .venv
.venv/bin/pip install pymodbus ocpp websockets aiohttp
.venv/bin/python -u charge_loop.py          # Ports: OCPP 9000, Web 8080
```

1. Firewall öffnen (nur LAN):
   `sudo ufw allow from <lan-subnetz> to any port 9000 proto tcp` (dito Port 8080)
2. Wallbox-App → OCPP aktivieren → URL `ws://<server-ip>:9000/`, Passwort leer
3. Web-UI: `http://<server-ip>:8080/`

## Projektstand & Struktur

| Datei | Zweck |
|---|---|
| [PLAN.md](PLAN.md) | Architektur, Entscheidungen, Meilensteine |
| [poc/read_sun2000.py](poc/read_sun2000.py) | M1 ✅ Modbus-Lesen (Register verifiziert, 10-min-Stabilität) |
| [poc/ocpp_server.py](poc/ocpp_server.py) | M2 ✅ OCPP-Server, Box verbunden, Kommandos akzeptiert |
| [poc/charge_loop.py](poc/charge_loop.py) | M3 ⏳ Regel-Loop + Web-UI (Ladetest mit Fahrzeug ausstehend) |
| [poc/README.md](poc/README.md) | Testprotokolle und Erfolgskriterien je Meilenstein |

Danach: M4 Vue-Dashboard, M5 Historie (SQLite), M6 Pi-Deployment (systemd).

## Lizenz

Beer-Ware (Revision 42) — siehe [LICENSE](LICENSE). Wer's nützlich findet und mich
trifft: ein Bier. Lizenzen der verwendeten Bibliotheken:
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
