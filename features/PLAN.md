# Plan: PV-Überschussladen auf dem Raspberry Pi

Ziel: Eigene App auf dem Raspberry Pi, die den PV-Überschuss der HUAWEI-Sun2000-Anlage misst und die Wallbox Pulsar Pro so regelt, dass das Auto möglichst nur mit Überschussstrom lädt.

---

## 0. Entscheidung: Eigenbau (fix)

Gründe:
- evcc verlangt für OCPP-/kommerzielle Wallboxen ein Sponsor-Token (4 $/Monat oder 150 € Lifetime); Pulsar Pro dort ohnehin nur über Fremd-Template mit bekannten Macken.
- Gewünscht ist eine exakt zugeschnittene Lösung: **nur die Features, die gebraucht werden — nichts darüber hinaus.**

Die evcc-Quellen (Open Source) bleiben nützliche Referenz für Modbus-Register und OCPP-Verhalten.

**Designprinzip:** Jedes Feature muss explizit gewollt sein. Keine Geräteabstraktionen für fremde Hardware, keine Konfigurierbarkeit auf Vorrat — der Code kennt genau diese drei Geräte (Smart Meter, Sun2000, Pulsar Pro) und diesen einen Haushalt.

## 1. Architekturüberblick

```
┌──────────────────┐               ┌──────────────────────────────┐
│ Sun2000 + SDongle│  Modbus TCP   │ Raspberry Pi                 │
│  ├─ PV-Leistung  │──────────────▶│  ├─ Modbus-Reader (Polling)  │
│  ├─ Batterie-SOC │   (Polling)   │  ├─ Regel-Logik (Loop)       │
│  └─ Power Sensor │               │  ├─ OCPP-Server (CSMS)       │
│     (Netzleistg.)│               │  ├─ REST-API (FastAPI)       │
└──────────────────┘               │  ├─ SQLite (Logging)         │
┌─────────────┐     OCPP 1.6J     │  └─ Web-UI (Vue 3)           │
│ Pulsar Pro  │◀──────────────────▶│                              │
└─────────────┘     (WebSocket)    └──────────────────────────────┘
```

Alles läuft lokal auf dem Pi, keine Cloud-Abhängigkeit.

## 2. Datenquellen

Für die Regelung braucht es zwei Werte: **Netzleistung** (Bezug/Einspeisung am Hausanschluss) und **Ladeleistung** der Wallbox. Die PV-Leistung des Wechselrichters ist nur fürs Dashboard nötig.

### 2a. Netzleistung: Huawei Smart Power Sensor per Modbus TCP (primär)

Vorhandener Zähler ist ein **PPC Smart Meter Gateway (SMGW, Deutschland)**. Dessen HAN-Schnittstelle scheidet für die Regelung aus:
- Standard: nur 15-Minuten-Werte, Zugriff per TLS/TRuDI umständlich.
- Mit TAF-14 (kostenpflichtig beim Messstellenbetreiber zu beauftragen): bestenfalls 1-Minuten-Werte — für eine Laderegelung zu träge.

Stattdessen: Die Anlage hat einen Batteriespeicher — Huawei-Installationen mit Speicher haben dafür praktisch immer einen **Smart Power Sensor (DTSU666-H)** am Netzanschlusspunkt. Dessen Werte sind über den Wechselrichter per Modbus TCP lesbar (~5 s Auflösung, ausreichend für die Regelung).

- **Vorab prüfen:** FusionSolar-App zeigt Netzbezug/Einspeisung live? → Sensor vorhanden.
- Register `37113` — Zähler-Wirkleistung am Netzanschluss (int32, W; Vorzeichen in M1 verifizieren).

Fallback, falls wider Erwarten kein Sensor verbaut: IR-Lesekopf (~30 €) auf der optischen INFO-Schnittstelle des Basiszählers hinter dem SMGW — SML-Protokoll, 1 s-Werte, PIN vom Messstellenbetreiber nötig.

### 2b. PV-Leistung & Batterie-SOC: Sun2000 per Modbus TCP

**Voraussetzungen**
- SDongle-A05 (WLAN/Ethernet-Dongle) mit aktueller Firmware; in den Dongle-Einstellungen "Modbus TCP" auf "uneingeschränkt" stellen (per FusionSolar-App / Installateurszugang).

**Zugriff**
- Modbus TCP, Port 502, Unit-ID des Wechselrichters (meist 1, ggf. 0/100 je nach Dongle-Firmware).
- Register (am 2026-07-15 gegen FusionSolar verifiziert; IP siehe LOCAL.md, Unit 1):
  - `32080` — Wechselrichter-Wirkleistung (int32, W) ✓
  - `37113` — Netzleistung vom Power Sensor (int32, W, **positiv = Einspeisung**) ✓
  - `37760` — Batterie-SOC (uint16, ×0,1 %) ✓
  - `37001` — Batterieleistung (int32, W, **positiv = lädt**) ✓
- **Einschränkungen:** Der Dongle erlaubt nur *einen* Modbus-TCP-Client gleichzeitig und ist träge — Polling-Intervall ≥ 5 s, Verbindungsabbrüche einplanen (Reconnect-Logik).

Damit kommt **alles aus einer Quelle** (ein Modbus-Poll liefert Netz, PV und Batterie) — kein zusätzlicher Adapter, keine SMGW-Bürokratie.

## 3. Wallbox-Steuerung: Pulsar Pro per OCPP

Die Pulsar Pro unterstützt OCPP 1.6J — der große Vorteil gegenüber der Pulsar Plus. Damit ist lokale Steuerung ohne Wallbox-Cloud möglich:

- Auf dem Pi läuft ein kleiner OCPP-Server (CSMS) mit der Python-Bibliothek [`ocpp`](https://github.com/mobilityhouse/ocpp) (The Mobility House), WebSocket z. B. `ws://<pi-ip>:9000/<charger-id>`.
- In der Wallbox-App die OCPP-URL des Pi eintragen; die Box verbindet sich dann selbstständig.
- Stromvorgabe über SmartCharging: `SetChargingProfile` (TxDefaultProfile, Limit in Ampere), Start/Stopp über `RemoteStartTransaction` / `RemoteStopTransaction`.
- Hinweis: Im OCPP-Modus sind Teile der Wallbox-App-Funktionen deaktiviert — bewusste Entscheidung.

Fallback (nicht empfohlen): inoffizielle Wallbox-Cloud-API — cloudabhängig, rate-limitiert, kann jederzeit brechen.

## 4. Regel-Logik (Kernstück)

Alle 5–10 s:

Anlage: 3-phasig, 16 A (11 kW) → Regelbereich **4,2–11 kW** (unter 3 × 6 A × 230 V ≈ 4,14 kW kann kein Auto laden, IEC 61851; die Pulsar Pro kann nicht auf 1-phasig umschalten).

Alle 5–10 s:

1. Messen: `netz_W` (Power Sensor via Modbus), `ladeleistung_W` (Wallbox-MeterValues).
2. Überschuss = Einspeisung + aktuelle Ladeleistung.
3. Sollstrom = Überschuss / (230 V × 3), gerundet auf ganze Ampere.
4. Begrenzen auf 6–16 A.
5. Glättung gegen Flattern:
   - Anpassungen höchstens alle 20–30 s.
   - Einschaltschwelle: Überschuss > ~4,2 kW für z. B. 2 Minuten.
   - Ausschaltverzögerung: 2–3 Minuten unter Schwelle, bevor gestoppt wird (Wolken!).

**Batterie-Priorität (Speicher vorhanden):** ergibt sich von selbst, wenn als Überschuss nur die *Einspeisung* zählt — der Wechselrichter lädt zuerst den Speicher, eingespeist wird erst, wenn der Speicher voll ist oder sein Ladelimit erreicht. Keine SOC-Logik nötig. SOC wird nur fürs Dashboard angezeigt.

**Lademodi (beschlossener Umfang)**
- **PV-Überschuss:** nur laden, wenn Überschuss reicht.
- **Min + PV:** immer mit 6 A (3-ph.) laden, Überschuss oben drauf.
- **Schnell:** volle 16 A, PV ignorieren.

**Freigabe (Knopfdruck):** Laden passiert grundsätzlich nur nach manueller Freigabe (Button im UI). Ohne Freigabe startet der Loop keine Transaktion — egal welcher Modus, auch nicht im Nachttarif-Fenster. Freigabe gilt, bis sie zurückgenommen wird.

## 4b. Nachttarif-Automatik (00:00–08:00 Uhr)

Günstiger Ladetarif zwischen 0 und 8 Uhr. In diesem Fenster, zeitgesteuert:

a) **Fahrzeug:** falls eingesteckt (und freigegeben) → volle Leistung (16 A), unabhängig von PV.
   Um 08:00 zurück in den vorher aktiven Modus.
b) **Hausbatterie (LUNA2000, 5 kWh):** falls SOC < 20 % um Mitternacht → aus dem Netz auf 80 % laden
   (~3 kWh, mit ~2,5 kW in gut einer Stunde erledigt), danach bzw. spätestens 08:00 beenden.

Umsetzung Batterie: Huawei-Register für erzwungenes Laden ("forcible charge") per Modbus-Write —
Registerblock um 47075–47086 (Ladeleistung, Ziel-SOC, Start/Stopp). **Vor Implementierung am Gerät
verifizieren** (Huawei "Modbus Interface Definitions", firmwareabhängig); zudem muss "Laden aus dem
Netz" in den Batterieeinstellungen erlaubt sein. Schreibzugriff = Modbus-TCP-Modus "uneingeschränkt"
(bereits aktiv).

Defaults (bis anders gewünscht): Freigabe-Pflicht gilt auch nachts; Batterie-Ladeleistung 2,5 kW.

## 5. Technologie-Stack

| Baustein | Wahl | Begründung |
|---|---|---|
| Sprache | Python 3.11+ | pymodbus + ocpp-Lib verfügbar, schnell entwickelt |
| Modbus | `pymodbus` (async) | Standard, Async passt zum Rest |
| OCPP | `ocpp` (Mobility House) | Referenzimplementierung für 1.6J |
| API | FastAPI + WebSocket | Live-Daten fürs UI |
| Frontend | Vue 3 + Vite | Name des Repos: **PVueb** 😉 |
| Datenbank | SQLite | Messwerte/Ladehistorie, kein Server nötig |
| Deployment | Docker Compose oder systemd | Autostart, Watchdog |

## 6. Meilensteine

1. **M1 – Mess-PoC:** Modbus-Skript liest Netzleistung (37113), PV-Leistung, Batterie-SOC. Vorzeichen und Werte gegen FusionSolar-App verifizieren. Vorab: Power Sensor vorhanden? Modbus TCP am Dongle freigeschaltet?
2. **M2 – OCPP-PoC:** Pulsar Pro verbindet sich mit dem Pi; Start/Stopp und Stromlimit funktionieren nachweisbar (Zangenamperemeter oder MeterValues).
3. **M3 – Regel-Loop:** Beide PoCs verbunden, Hysterese/Verzögerungen, Modus fest "PV-Überschuss". Erste echte Überschussladung.
4. **M3b – Freigabe + Nachttarif:** Freigabe-Flag im Loop; Zeitfenster-Logik 00–08 Uhr (Auto volle Leistung, Rückkehr in vorherigen Modus); Batterie-Zwangsladung inkl. Verifikation der Schreibregister am Gerät.
5. **M4 – API + UI:** FastAPI-Backend, Vue-Dashboard (Live-Leistungsfluss, Modus-Umschalter, **Freigabe-Button**, Ladestatus).
6. **M5 – Persistenz + Historie:** SQLite-Logging, Tages-/Ladevorgangs-Charts.
7. **M6 – Betrieb:** systemd/Docker, Auto-Reconnect, Watchdog, Log-Rotation, Update-Weg.

## 7. Risiken / Stolpersteine

- **Power Sensor nicht verbaut:** Unwahrscheinlich (Speicher braucht ihn), aber vorab in FusionSolar prüfen. Fallback: IR-Lesekopf am Basiszähler.
- **Nur ein Modbus-Client:** Läuft schon etwas anderes gegen den Dongle (Home Assistant o. Ä.), blockiert das den Zugriff. Ggf. Modbus-Proxy einplanen.
- **Vorzeichen der Zählerwerte:** Einspeisung positiv oder negativ? Unbedingt in M1 gegen die FusionSolar-Anzeige prüfen.
- **Regelbereich beginnt bei ~4,2 kW:** 3-phasig/16 A heißt: bei weniger Überschuss lädt das Auto nicht. Bewusste Einschränkung; Phasenumschaltung ginge nur mit externem Schütz.
- **Dongle-Firmware:** Ältere SDongle-Firmware kann Modbus TCP gar nicht oder nur eingeschränkt — vorher prüfen/aktualisieren.
- **OCPP-Registrierung:** Wallbox muss den Pi im selben Netz erreichen; feste IP für den Pi vergeben.
- **Autos mit Lade-Eigenheiten:** Manche Fahrzeuge wachen nach Ladepause nicht zuverlässig auf — Stopp-Strategie testen.
