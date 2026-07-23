# issue

Die Application läuft fehlerfrei im Modus PVEuberschußlöaden min 6A
Die Wallbox wird mit dem fahrzeug verbunden, die PV leistung ist aber zu gering, es kommt nicht zum ladeprozeß
Dann wird das Fenster 0:00 bis 8:00 erreicht, also "nacht-Laden mit voller Leistung"
Das Fahrzeug startet tatsächlich mit dem Laden, aber nur mit 4 KW, also 6A
Tatsächlich ist dann nach manueller Kontrolle in der Wallbox die Ladeleistung auf 6A begrenzt.
Interessanterweise ist im Smartphone auch eine neue Authentifizierung notwendig
In den FusionSolar-Daten ist das Ladeverhalten auch zu sehen, von 0:00 - 0::40 ist ein 4 KW Ladeleistung zu sehen.
Dann habe ich manuell den Ladestrom in der Wallbox-App auf 16 A hochgesetzt, und die Ladung lief dann mit 11 KW zu Ende

Issue:
Bei starten der Nachtladung muß die Ladeleistung auf 16 A hochgesetzt werden. Theoretisch könnte die Ladeleistung immer pauschal auf maximum gesetzt werden, wenn die Box "gesperrt" wird, also kein PV laden durchgeführt wird. Damit bei der nicht überwachten nachtladung immer mit 16 A geladen wird.

Eventuell Ladedaten könntest Du vom 192.168.100.2 herunterladen

---

## Was die Logs sagen (2026-07-23, `docker logs pvueb` vom Pi)

Vorhanden sind der 19.–21.07.; die Nacht 19.→20.07. ist die einzige mit einem
angesteckten Fahrzeug. Der Befund passt **nicht** zur ersten Vermutung:

- **Der Regler hat nie 6 A gesetzt.** Im gesamten Log kommt
  `Limit gesetzt: 6.0 A` kein einziges Mal vor. Gesetzt wurden 7,1–9,1 A
  (PV-Regelung am Tag) und 16,0 A (Nachtfenster). Die 6 A der Box stammen also
  nicht aus dem `minpv`-Betrieb.
- **Das Nachtfenster hat sauber gearbeitet.** Ab 00:00:04 ging alle paar Minuten
  `Limit gesetzt: 16.0 A` raus, über Stunden.
- **Die Box hat jeden Start abgelehnt:** `RemoteStart: Rejected`, Box-Status
  `Preparing`. Zwischen 00:00 und 06:52 kam kein einziges `Charging`.
- **Geladen wurde trotzdem** — laut FusionSolar 00:00–00:40 mit 4 kW. Der Regler
  hat davon nichts gesehen: erst um 06:52:32 meldet die Box neun Sekunden lang
  `Charging`, dann `Finishing` und `Transaktion beendet (meter 7425880 Wh)`.
- **Die Box bootet täglich gegen 07:23**, also nach der Ladung, nicht davor.

Daraus folgt eine andere Erklärung als zunächst angenommen: **die Ladung lief
außerhalb der OCPP-Transaktion des Reglers.** Die Box hat selbst gestartet (RFID
oder App) und dabei ihre eigene Stromgrenze verwendet — den App-Slider, der laut
[README](../README.md#hardware-voraussetzungen) zusätzlich zum OCPP-Limit
deckelt. Ein `TxDefaultProfile` greift auf so eine Session nicht zu; genau
deshalb half das manuelle Hochsetzen in der App sofort.

Beweisen lässt sich das nicht abschließend, weil der Mitschnitt nicht lief
(`PVUEB_RECORD_DIR=` leer). Ohne ihn gibt es keine Zeitreihe von `charge_w`
gegen `current_limit`.

## Zuerst zu tun

1. **App-Slider der Wallbox auf 16 A** und dort lassen. Er deckelt unabhängig
   vom OCPP-Profil und ist der wahrscheinlichste Grund für die 4 kW.
2. **Mitschnitt einschalten**: `PVUEB_RECORD_DIR=/data/recordings` in der `.env`
   auf dem Pi. Ohne ihn ist die nächste solche Nacht genauso wenig nachweisbar.
3. Prüfen, warum die Box jeden `RemoteStart` mit `Rejected` beantwortet, obwohl
   ein Fahrzeug angesteckt ist — solange das so bleibt, regelt PVueb gar nicht,
   sondern die Box lädt nach ihren eigenen Einstellungen.

## Behoben

- `on_boot` erklärt den Merker für ungültig (`limit_known = False`)
- Limit geht spätestens alle `PVUEB_LIMIT_REFRESH_S` (300 s) erneut raus, auch
  unverändert — das Totband der PV-Regelung verschluckt die Auffrischung nicht
- bei laufender Transaktion als `TxProfile` mit `transaction_id` und höherem
  `stack_level`, sonst wie bisher als `TxDefaultProfile`
- ohne Freigabe wird auf `MAX_AMPS` gestellt, damit eine per RFID oder
  Hersteller-App gestartete Ladung nicht mit dem gedrosselten PV-Limit läuft
  (der Vorschlag oben)
- fließt weniger als `PVUEB_LIMIT_WARN_FACTOR` × erlaubt, kommt eine Warnung ins
  Log und `limit_effective` im Status auf `false`; Mitschnitt führt das Feld mit
- Regressionsszenarien: `poc/test_sim.py 19 20`

Was das **nicht** kann: eine Box, die das Profil grundsätzlich ignoriert, lässt
sich per OCPP nicht zwingen. Dann bleibt nur die Warnung — und der Blick in die
Hersteller-App, deren Slider zusätzlich deckelt.