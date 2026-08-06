# PVueb – PV-Überschussladen

Maßgeschneiderte App für Solar-Überschussladen auf dem Raspberry Pi: liest die
PV-Anlage (HUAWEI Sun2000) per Modbus TCP aus und steuert die Wallbox
(Wallbox Pulsar Plus) lokal per OCPP 1.6J — ohne Cloud, ohne Abo, ohne Fremd-Backend.

Bewusst minimal: Der Code kennt genau diese drei Geräte und einen Haushalt.
Keine Geräteabstraktionen, keine Konfigurierbarkeit auf Vorrat.

> Wer eine Lösung für beliebige Hardware sucht, ist mit [EVCC](https://evcc.io)
> besser bedient. Dieses Projekt ist auf eine einzige Anlage zugeschnitten.

Warum es trotzdem existiert, was an ihm schlecht ist und was das mit dem Zustand
freier Software zu tun hat, steht im **[Prüfbericht in eigener Sache](docs/selbstkritik.html)**
([Fassung im Browser lesen](https://claude.ai/code/artifact/cff0080a-0d2c-4f02-bdb5-b2684e64341c)).

## Regelprinzip

Alle fünf Sekunden werden drei Register des Wechselrichters gelesen —
Netzleistung, Batterieleistung, PV-Erzeugung — und daraus der verfügbare
Überschuss berechnet:

```
Überschuss = Netzleistung + Ladeleistung + Batterieleistung
```

Die eigene Ladung wird also wieder herausgerechnet, sonst hielte der Regler sie
für einen PV-Einbruch. Die Batterieleistung geht mit ein, weil eine ladende
Hausbatterie Leistung freigibt, die das Auto bekommen könnte — eine entladende
dagegen täuscht Überschuss nur vor.

Geregelt wird auf den **gleitenden Mittelwert** dieses Überschusses, gedeckelt
auf den Momentanwert. Zwei Fenster: ein kurzes für die Startentscheidung, ein
langes für die laufende Ladung. So folgt das Limit nicht jeder Wolke, startet
aber trotzdem zügig.

## Funktionen

- **Freigabe per Knopfdruck** — ohne manuelle Freigabe lädt nichts, auch nachts nicht
- **Zwei Lademodi:**
  - *PV + Minimum* (`minpv`) — hält einen einstellbaren Mindeststrom (Slider 6–16 A),
    Überschuss oben drauf. Gestartet wird erst ab dem 1,10-fachen dieser Leistung
  - *Sofort laden* (`fast`) — volle Leistung, PV egal

  Einen dritten Modus „reines PV-Überschussladen" gab es bis zum 30.07.2026. Er
  ist entfallen, weil er nichts anderes konnte: Wallbox und Fahrzeug brauchen
  ohnehin den Mindeststrom von 6 A × 3 × 230 V ≈ 4,1 kW, unterhalb davon lädt
  nichts. Eine Sicherung, die ihn noch nennt, startet in `minpv`.
- **Wolkenloch-Überbrückung** (`minpv`): Fällt der Überschuss unter eine Pause-Schwelle,
  läuft ein Timeout statt eines sofortigen Stopps. Erholt er sich vorher, läuft die
  Ladung durch. So wird aus einer vorbeiziehenden Wolke kein Start-Stopp-Zyklus.
- **Batterie-Boost**: Während einer laufenden Ladung schiebt die Hausbatterie bis zu
  einer einstellbaren Leistung nach, statt das Ladelimit herunterzuregeln — mit
  Tagesbudget und SOC-Untergrenze.
- **Dauer-Boost aus voller Batterie**: Steht die Hausbatterie über 90 % (einstellbar),
  schiebt sie dauerhaft 1000 W ins Auto, bis sie auf 50 % abgesunken ist — sonst ginge
  der Überschuss eines sonnigen Tages für ein paar Cent ins Netz. Die beiden Schwellen
  sind eine Hysterese und ersetzen ein Tagesbudget. Nachgeladen wird die Batterie im
  normalen Ablauf wieder, spätestens abends unter der Mindestladeleistung. Details:
  [features/feature_PermanentBoost.md](features/feature_PermanentBoost.md)
- **Starthilfe aus der Batterie**: Fehlen zur Startschwelle nur ein paar hundert Watt,
  füllt eine ausreichend volle Batterie sie auf (Standard 500 W ab 50 % SOC), statt die
  Ladung ganz ausfallen zu lassen. Gefüllt wird nur die Lücke — reicht die PV, kostet
  die Starthilfe nichts. Sie zählt auf dasselbe Tagesbudget wie der Boost.
- **Nachttarif-Automatik** (abschaltbar): im Tariffenster (Standard 00:00–08:00)
  lädt ein freigegebenes Fahrzeug mit voller Leistung
- **Hausbatterie aus dem Netz laden** (LUNA2000-Zwangsladung, Register verifiziert,
  live getestet): per Web-Toggle oder Automatik — nachts bei SOC unter Schwelle, aber
  nur, wenn die Sonnenprognose (forecast.solar) für den nächsten Tag darunter liegt.
  Lädt bis Ziel-SOC, der Wechselrichter stoppt selbst. Details:
  [features/feature_NachtStromInHuaweiBatterie.md](features/feature_NachtStromInHuaweiBatterie.md)
- **Ladeleistung notfalls geschätzt**: Meldet die Wallbox keine Momentanleistung, wird
  sie aus dem Energiezähler abgeleitet und andernfalls aus dem gesetzten Limit
  geschätzt — gedeckelt auf PV minus Netz minus Batterie. Ohne das verwechselt der
  Regler die eigene Ladung mit einem Wolkenloch (siehe [Diagnose](#diagnose)).
- **Startversuche mit Backoff**: Ob wirklich Strom fließt, entscheidet das Fahrzeug.
  Lehnt es ab, wächst der Abstand zwischen Versuchen, statt im Regeltakt zu funken.
  Hängt die Box in `Finishing`, wird einmal ein `ChangeAvailability`-Zyklus versucht.
- **Das Limit wird nachgehalten**, nicht nur einmal gesetzt: spätestens alle
  `PVUEB_LIMIT_REFRESH_S` (Standard 300 s) geht es erneut raus, nach einem Boot der
  Box sofort, und bei laufender Ladung als `TxProfile` statt `TxDefaultProfile`.
  Fließt trotzdem deutlich weniger als erlaubt, steht eine Warnung im Log und
  `limit_effective` im Status auf `false`. Ohne das lud das Auto eine Nacht lang
  mit 6 A statt 16 A, während der Regler 16 A für gesetzt hielt
  ([docs/issue_limit_to_6A.md](docs/issue_limit_to_6A.md)).
- **Neustart ohne Ladeunterbrechung**: Der Dienst sichert seinen Laufzeitzustand bei
  jeder Änderung und zusätzlich jede Minute — mit SHA-256 in der Kopfzeile gegen
  beschädigte Dateien und, weil der Pi keine gepufferte Uhr hat, mit Boot-Kennung,
  Systemlaufzeit und dem NTP-Zustand des Kernels gegen eine springende Uhr.
  Startet er binnen 10 Minuten neu,
  übernimmt er Freigabe, Modus und vor allem die laufende OCPP-Transaktion und regelt
  weiter — Wallbox, Wechselrichter und Auto merken nichts. Ältere Sicherungen gelten als
  beendete Sitzung und werden verworfen. Details:
  [features/feature_NeustartOhneUnterbrechung.md](features/feature_NeustartOhneUnterbrechung.md)
- **Startverhalten ohne Sicherung**: freigegeben, Modus `minpv` mit 6 A. Die Anlage
  arbeitet nach einem Strom- oder Softwareausfall von allein weiter.
- **Web-UI** fürs Handy, sechs Seiten zum Wischen — nach Frage sortiert, nicht nach
  Datenquelle. Vorneweg die *Übersicht*: ein Bild der Anlage mit Sonne, Netz, Haus,
  Akku, Wallbox und Auto, dazwischen die Energieflüsse als laufende Punkte —
  Richtung, Farbe und Tempo sagen, wohin wie viel geht. Das Kabel zum Auto ist nur
  da, wenn eines steckt, und gestrichelt, solange die Box in `Preparing` auf eines
  wartet. Jedes Element ist ein Sprung auf seine Detailseite; die Herzen zeigen, ob
  Regeltakt, Modbus und OCPP noch schlagen. Dahinter unverändert:
  *Status & Lademodus* (Netz, Überschuss, Ladeleistung, Freigabe, Modus,
  Nachtautomatik, Betriebsstufe in einer Zeile) · *Huawei-Batterie* (SOC, Leistung,
  Netzladung, Prognose) · *Steuerung* — warum lädt es gerade so (PV-Erzeugung,
  Hausverbrauch, was fürs Auto frei ist, PV-Mittel, minpv-Schwellen, Boosts,
  Netzanteil) · *Diagnose* — lebt der Dienst noch (Alter des letzten Regeltakts,
  Heartbeats, Version, Sitzung, Heartbeat-Schieberegler) · *Wallbox-Cloud* als
  Referenzmessung ganz hinten. Dazu `/info`: Commit, Bauzeit, Laufzeit und was der
  Start aus der Sitzungssicherung gemacht hat.
- **Ladeleistung als Referenz** (optional, `PVUEB_WALLBOX_*`): Die Pulsar Plus meldet ihre
  Ladeleistung nicht über OCPP, liefert sie aber an die Hersteller-Cloud
  ([API-Doku](https://github.com/SKB-CGN/wallbox)). Von dort abgerufen dient sie nur der
  Kontrolle — die Regelung nutzt den Wert nicht, weil die Cloud alle 30 s aktualisiert
  und ein Internetausfall keine Ladung beeinflussen darf. Status und Mitschnitt führen
  die Differenz zur Schätzung als `charge_w_abweichung` mit.
- **Wächter über dem Regeltakt**: Ein fehlgeschlagener Takt kostet einen Takt, eine
  abgestürzte Nebenaufgabe läuft neu an, und bleibt der Takt trotzdem aus, beendet ein
  eigener Thread den Prozess, damit Docker neu startet. Das Alter des letzten Takts steht
  in der UI und im Docker-Healthcheck — ohne diese Zahl sieht ein Dienst, der nur noch
  Messwerte anzeigt, genauso gesund aus wie einer, der regelt
  ([docs/issue_nightly_load_did_not_work.md](docs/issue_nightly_load_did_not_work.md))
- **Meldungen aufs Telefon** (optional, `PVUEB_NOTIFY_URL`): Der Wächter macht aus
  einem stehenden Regeltakt zwei Minuten statt siebzehn Stunden — sagen musste er es
  aber erst lernen. PVueb schickt ein JSON an eine konfigurierbare Adresse und
  vergisst es; wer daraus eine Nachricht macht, ist nicht seine Sache.
  **Alarm** bei Neustart, weggefallenem Wechselrichter und weggefallener Wallbox,
  **Info** bei Rückkehr, angestecktem Fahrzeug, Start und Ende von Nacht- und
  PV-Laden sowie der Netzladung des Hausakkus. Kein `await` im Regelpfad, eigene
  überwachte Aufgabe, nie ein Fehler nach oben. Details:
  [features/feature_Benachrichtigung.md](features/feature_Benachrichtigung.md)
- **Mitschnitt** (optional): Der Regelbetrieb wird als JSON-Zeilen mitgeschrieben, eine
  Datei pro Tag, ältere Tage fallen automatisch heraus. Daraus baut
  [poc/curve_from_recording.py](poc/curve_from_recording.py) echte Kurven für die
  Simulation.

## Hardware-Voraussetzungen

| Gerät | Anforderung |
|---|---|
| HUAWEI Sun2000 | SDongle mit Modbus TCP „uneingeschränkt" freigeschaltet |
| Smart Power Sensor (DTSU666-H) | am Netzanschlusspunkt (bei Speicher-Anlagen Standard) |
| Wallbox Pulsar Plus | OCPP aktiviert, URL zeigt auf diesen Server; **App-eigene Zeitpläne löschen**, App-Slider auf 16 A |
| Server | Raspberry Pi oder beliebiger Linux-Rechner im selben LAN |

Der SDongle nimmt **nur eine Modbus-Verbindung** an. Läuft PVueb, kommt kein zweites
Werkzeug mehr an den Wechselrichter — für Registertests muss der Dienst kurz gestoppt
werden.

## Einrichtung

### Docker auf dem Raspberry Pi (empfohlen)

```bash
git clone git@github.com:MerowingerX/PVUeberschussLaden.git pvueb
cd pvueb
cp .env.example .env          # PVUEB_INVERTER_IP eintragen (IP des SDongle)
docker network create myhome  # einmalig, siehe unten
docker compose up -d --build
docker compose logs -f        # Box-Boot und Modbus-Werte beobachten
```

> **Das Netz muss vor dem ersten Start existieren.** Die
> [docker-compose.yml](docker-compose.yml) verweist auf das externe Netz `myhome`
> — darüber ist die Meldestelle erreichbar. Fehlt es, verweigert
> `docker compose up` den Dienst, und dann regelt nichts mehr. Wer keine
> Meldungen will, kann den `networks:`-Block stattdessen entfernen.

Startet automatisch neu (`restart: unless-stopped`), auch nach Pi-Reboot.
Zeitzone im Container: `Europe/Berlin` (Dockerfile) — wichtig fürs Nachttarif-Fenster.
Nach Umzug auf den Pi: OCPP-URL in der Wallbox-App auf die Pi-IP ändern.

Zwei Volumes sind in [docker-compose.yml](docker-compose.yml) vorbereitet: die `.env`
selbst (damit der Dienst sie vervollständigen kann) und `./recordings` für den
Mitschnitt — dafür `PVUEB_RECORD_DIR=/data/recordings` setzen, sonst ist er nach dem
nächsten `--build` weg.

Beide Ports werden derzeit an alle Schnittstellen gebunden. Steht der Pi in einem Netz,
dem du nicht vollständig traust, im Compose-File die LAN-Adresse voranstellen
(`"192.168.x.y:8080:8080"`) — Docker umgeht `ufw`-Regeln über eigene iptables-Ketten.

### Direkt (Entwicklung)

```bash
cd poc
python -m venv .venv
.venv/bin/pip install -r ../requirements.txt
.venv/bin/python -u charge_loop.py          # Ports: OCPP 9000, Web 8080
```

1. Wallbox-App → OCPP aktivieren → URL `ws://<server-ip>:9000/`, Passwort leer
2. Web-UI: `http://<server-ip>:8080/`

## Sicherheit

**Beide Ports gehören ins LAN und nirgendwo sonst.** Wer sie erreicht, kann die Wallbox
steuern und die Hausbatterie aus dem Netz laden.

```bash
sudo ufw allow from <lan-subnetz> to any port 9000 proto tcp
sudo ufw allow from <lan-subnetz> to any port 8080 proto tcp
```

Docker umgeht `ufw`-Regeln über eigene iptables-Ketten — auf dem Pi die Ports in
`docker-compose.yml` deshalb an die LAN-Adresse binden (`192.168.x.y:8080:8080`)
statt an alle Interfaces. Kein Port-Forwarding im Router.

**Anmeldung** ist optional und standardmäßig aus, weil das Gerät im eigenen LAN steht:

```bash
PVUEB_WEB_USER=frank          # Basic Auth für die Web-UI
PVUEB_WEB_PASSWORD=…
PVUEB_OCPP_USER=wallbox       # Basic Auth für den OCPP-Endpunkt
PVUEB_OCPP_PASSWORD=…
```

Sobald `*_USER` gesetzt ist, wird geprüft; bleibt es leer, ändert sich nichts.
Beim OCPP-Zugang die Reihenfolge beachten: **erst** Benutzer und Passwort in der
Wallbox-App eintragen, **dann** die `.env` setzen und neu starten — sonst kommt die
Box nicht mehr herein.

Zugangsdaten (Wechselrichter-IP, myWallbox-Konto) stehen in `.env`, Notizen zur eigenen
Anlage in `LOCAL.md` — beide sind gitignored und gehören nicht ins Repository.

## Konfiguration

```bash
cp .env.example .env        # PVUEB_INVERTER_IP eintragen
```

**Die `.env` vervollständigt sich selbst.** Beim Start — und nach jeder Änderung im
Web-UI — schreibt der Dienst sie zurück: jede Einstellung mit dem Wert, der gerade
wirklich gilt, dazu eine Zeile Erklärung. Es genügt also, die Wechselrichter-IP
einzutragen; alles andere steht nach dem ersten Start vollständig da und lässt sich
dort ändern. Nebeneffekt: Mindeststrom und Heartbeat aus dem Web-UI überleben den
Neustart.

Vor jedem Schreiben entsteht eine Sicherung in `.env.bak`, geschrieben wird über eine
temporäre Datei — eine halb geschriebene `.env` kann es nicht geben. Eigene Variablen
bleiben in einem eigenen Abschnitt am Ende erhalten. Ist die Datei schreibgeschützt,
läuft der Regler normal weiter und meldet es nur im Log.

Im Docker-Betrieb passiert das nur, wenn die Datei zusätzlich eingebunden ist —
`env_file:` allein reicht dafür nicht, weil sie dann nur auf dem Host liegt:

```yaml
volumes:
  - ./.env:/app/.env
```

Die vollständige Liste mit Defaults und Erklärungen steht in
**[.env.example](.env.example)** — hier nur die Bereiche:

| Bereich | Was sich einstellen lässt |
|---|---|
| Standort & Anlage | Koordinaten, kWp, Neigung, Azimut (für die Sonnenprognose) |
| Nachttarif | Fenster in `HH:MM`, auch über Mitternacht |
| Regelzeiten | Poll-Takt, Mindestabstand für Limit-Änderungen, Verzögerung vor Start und Stopp |
| minpv-Trigger | Start-, Pause- und Resume-Faktor, Timeout für Wolkenlöcher |
| Mittelung | Fensterbreite beim Regeln und beim Starten |
| Ladelimit | Raster und Totzone der Limit-Anpassung |
| Batterie-Boost | Leistung, Tagesbudget, SOC-Untergrenze, Starthilfe (Leistung und SOC) |
| Batterie-Netzladung | Start-SOC, Ziel-SOC, Ladeleistung, Prognoseschwelle |
| Messwerte der Box | Höchstalter der gemeldeten Ladeleistung |
| Startversuche | Grundabstand des Backoffs |
| Mitschnitt | Zielordner, Abtastung, Aufbewahrungsdauer |
| Anmeldung | Basic Auth für Web-UI und OCPP (leer = offen) |
| Wallbox-Cloud | Konto, Charger-ID, Abrufintervall (Referenzmessung) |
| Betriebssicherheit | Geduld des Wächters, bis der Prozess neu startet |

Bewusst fest im Code (`poc/charge_loop.py`, siehe Designprinzip):

| Parameter | Standard | Bedeutung |
|---|---|---|
| `PHASES`, `VOLTAGE` | 3, 230 V | Anschluss der Wallbox |
| `MIN_AMPS`, `MAX_AMPS` | 6, 16 A | Regelbereich (IEC 61851 / 11-kW-Box) |

## Simulation und Tests

[poc/test_sim.py](poc/test_sim.py) fährt den echten Regelcode gegen ein Anlagenmodell —
ohne Wechselrichter, ohne Wallbox, ohne Auto. 22 Szenarien: sonniger Tag, Wolkenfelder,
Dauerflackern, Nachtfenster, leere und volle Hausbatterie, Wallbox ohne Leistungsmeldung,
Fahrzeug das nichts annimmt, Box die den Start ablehnt.

```bash
make test                             # alle drei Testskripte
cd poc
python test_sim.py                    # alle Szenarien mit Kurven und Ereignissen
python test_sim.py --json daten.json  # Rohdaten für eigene Auswertung
```

Daneben zwei Skripte, die nicht regeln, sondern prüfen, ob der Regler überhaupt noch
regelt und ob er es sagt: [poc/test_robust.py](poc/test_robust.py) (16 Testfälle gegen
den Ausfall vom 28.07.2026) und [poc/test_melden.py](poc/test_melden.py) (37 Prüfungen
der Meldungen, ohne Wallbox, Wechselrichter und Uhr).

Die Ergebnisse aller Szenarien mit Kurven und Ereignissen liegen als
**[Prüfbericht](docs/pruefbericht.md)** bei.

Aus einem Mitschnitt wird ein Szenario mit echtem Wetter:

```bash
python curve_from_recording.py ../recordings/status-2026-07-18.jsonl --from 10:00 --to 18:00
```

Die Grenze dieser Tests: Das Anlagenmodell stammt vom selben Autor wie der Regler. Wo
die Vorstellung von der Anlage falsch ist, ist sie in beiden falsch. Mitgeschnittene
Tage sind deshalb wertvoller als synthetische Kurven.

## Diagnose

Die Web-UI zeigt unter den Details die Werte, an denen sich Fehlverhalten ablesen lässt.

**Ladeleistung** — dahinter steht die Quelle:

| Anzeige | Bedeutung |
|---|---|
| ohne Zusatz | Die Box meldet ihre Momentanleistung, alles normal |
| `(Energie)` | Aus der Differenz des Energiezählers abgeleitet |
| `(geschätzt)` | Die Box meldet nichts — Wert kommt aus dem Limit, gedeckelt auf PV minus Netz minus Batterie |

**Hausverbrauch** — muss positiv sein. Negative Werte heißen, dass die geschätzte
Ladeleistung zu hoch liegt.

**Box-Status (OCPP)** — die wichtigsten Zustände:

| Status | Bedeutung |
|---|---|
| `Charging` | Es fließt Strom |
| `Preparing` | Stecker drin, bereit, wartet auf Startbefehl |
| `SuspendedEV` | Box gibt frei, Fahrzeug nimmt nichts an — in der Regel voll |
| `Finishing` | Transaktion beendet, Stecker steckt noch; manche Boxen hängen hier fest |

Im Log stehen die Gegenstücke dazu: `Start abgelehnt (…)` mit dem nächsten
Versuchsabstand, `Wallbox meldet keine Ladeleistung`, und beim Start einmalig
`Blockread 37001+114 nicht möglich`, falls der SDongle keine Sammelabfrage beherrscht
(dann werden die Register einzeln gelesen — funktioniert, zappelt aber mehr).

### Cloud-Zugang einzeln prüfen

Die Anbindung lässt sich testen, ohne den Regler zu starten:

```bash
python poc/read_wallbox_cloud.py          # Login, Charger-ID, Ladeleistung
python poc/read_wallbox_cloud.py --watch  # alle 30 s, bis Strg-C
```

Es liest die Zugangsdaten aus der `.env` und geben nie ein Passwort aus.
`read_wallbox_cloud.py` ermittelt die Charger-ID selbst — sie muss also nicht
vorab bekannt sein.

Häufige Befunde:

| Beobachtung | Bedeutung |
|---|---|
| `charging_power` 0 bei Status 181 | Box gibt frei, Fahrzeug nimmt nichts — meist voll |
| `state_of_charge: None` | Normal bei AC ohne ISO 15118; die Box kennt den Ladestand nicht |
| `max_charging_current` < 16 | Der Schieberegler in der Wallbox-App begrenzt; im OCPP-Betrieb überschreibt unser Limit ihn, sonst nicht |

## Projektstand & Struktur

| Datei | Zweck |
|---|---|
| [poc/charge_loop.py](poc/charge_loop.py) | ✅ Regel-Loop, OCPP-Server, Web-UI, Mitschnitt — der eigentliche Dienst |
| [poc/test_sim.py](poc/test_sim.py) | ✅ 22 Simulationsszenarien gegen den echten Regelcode |
| [poc/test_robust.py](poc/test_robust.py) | ✅ 16 Regressionstests gegen den Ausfall vom 28.07.2026 |
| [poc/test_melden.py](poc/test_melden.py) | ✅ 37 Prüfungen der Meldungen nach draußen |
| [app/](app/) | ✅ Android-Hülle: Regler-UI und Meldungen als zwei Reiter |
| [poc/curve_from_recording.py](poc/curve_from_recording.py) | ✅ macht aus Mitschnitten PV-Kurven für die Simulation |
| [poc/record_status.py](poc/record_status.py) | Mitschnitt von außen über HTTP (der Dienst kann es selbst) |
| [poc/read_wallbox_cloud.py](poc/read_wallbox_cloud.py) | ✅ myWallbox-Zugang prüfen, Charger-ID ermitteln |
| [poc/read_sun2000.py](poc/read_sun2000.py) | ✅ Modbus-Registertest (Register verifiziert) |
| [poc/ocpp_server.py](poc/ocpp_server.py) | ✅ OCPP-Server, Vorstufe des Regel-Loops |
| [poc/read_battery_registers.py](poc/read_battery_registers.py) | ✅ LUNA2000-Steuerregister verifiziert (nur lesend) |
| [poc/README.md](poc/README.md) | Testprotokolle und Erfolgskriterien je Meilenstein |

Live im Einsatz seit Juli 2026: Regelung, Wolkenüberbrückung, Boost und Nachtladen
laufen an einer 7-kWp-Anlage.

Offen: der **Totmannschalter** — solange er fehlt, bleibt „Pi tot, Strom weg" der eine
Fall, den niemand meldet ([features/feature_Benachrichtigung.md](features/feature_Benachrichtigung.md)).
Dazu die **Wallbox-Rückfallebene**
([features/feature_WallboxRueckfallebene.md](features/feature_WallboxRueckfallebene.md)),
Historie und Auswertung über längere Zeiträume und ein Dashboard jenseits der Handy-UI.

## Lizenz

Beer-Ware (Revision 42) — siehe [LICENSE](LICENSE). Wer's nützlich findet und mich
trifft: ein Bier. Lizenzen der verwendeten Bibliotheken:
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
