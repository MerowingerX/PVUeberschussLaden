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

## Analyse (2026-07-23)

Der Nachtzweig in `control_step` hat das Richtige gewollt:

```python
if state.charging:
    if state.current_limit != MAX_AMPS:
        await charge_point.set_limit(MAX_AMPS)
```

Nur hängt alles an `state.current_limit` — einem Merker, der still von der Box
abweichen kann. Drei Wege dorthin, alle passen zum beobachteten Ablauf:

1. **Die Box hat neu gebootet.** Die neue Authentifizierung im Smartphone deutet
   genau darauf. `on_boot` hat den Merker nicht zurückgesetzt, also glaubte der
   Regler weiter an sein zuletzt gesetztes Limit und schickte nie wieder eines.
2. **`TxDefaultProfile` bei laufender Transaktion.** OCPP 1.6 verlangt, dass es
   auch auf eine laufende Ladung wirkt; Boxen halten sich unterschiedlich streng
   daran. Die 6 A stammen aus dem `minpv`-Betrieb davor.
3. **Kein Nachfassen.** Einmal „Accepted", nie wieder geprüft. Ob das Profil
   wirklich greift, hat der Regler nie erfahren.

Die Simulation reproduziert das Symptom exakt: eine Box, die das Profil
quittiert aber bei 6 A bleibt, lädt 4,1 kWh pro Stunde — die 4 kW aus den
FusionSolar-Daten.

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