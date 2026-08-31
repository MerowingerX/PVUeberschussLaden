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

## Der Logauszug der Nacht (2026-07-23)

Aus `/var/lib/docker/containers/<id>/<id>-json.log` auf dem Pi. **Nicht** über
`docker logs`: das bricht an den pymodbus-Hexdumps ab und zeigte nur bis zum
21.07., was zu einer falschen Zwischenanalyse geführt hat. Die Rohdatei lesen.

```
00:00:04  Limit gesetzt: 16.0 A          TxDefaultProfile, 2 s vor der Transaktion
00:00:04  RemoteStart: Accepted
00:00:05  Wallbox-Status: SuspendedEV
00:00:06  Transaktion gestartet (meter 7425880 Wh)
00:00:09  Wallbox-Status: Charging
00:36:50  Modus (Web): fast              manuelle Eingriffe, ohne Wirkung
00:43:53  Modus (Web): minpv
03:50:52  Wallbox-Status: SuspendedEV    Ladung vorbei
03:50:55  Limit gesetzt: 16.0 A          erst jetzt wieder
```

Zwischen 00:00:04 und 03:50:55 liegt **keine einzige** weitere Limit-Nachricht.
Drei Stunden fünfzig Ladung, ein einziger Sendeversuch — und der ging raus,
bevor es die Transaktion überhaupt gab.

Damit ist die Ursache belegt:

1. Das Limit geht als `TxDefaultProfile` raus, während noch keine Transaktion
   läuft. Die Box eröffnet ihre Session danach und nimmt dabei ihren eigenen
   Wert (6 A) statt des Profils.
2. `current_limit` steht auf 16, also ist `state.current_limit != MAX_AMPS`
   falsch und der Nachtzweig schweigt für den Rest der Ladung.
3. Die Modus-Klicks um 00:36–00:43 konnten nichts ausrichten: `fast` und
   Nachtfenster laufen in denselben Zweig, der aus demselben Grund nichts tut.

Der Regler hat übrigens nie 6 A gesetzt — `Limit gesetzt: 6.0 A` kommt im
gesamten Log nicht vor. Die 6 A sind der Eigenwert der Box.

## Noch zu tun

**Mitschnitt einschalten**: `PVUEB_RECORD_DIR=/data/recordings` in der `.env`
auf dem Pi steht leer. Damit gäbe es eine Zeitreihe von `charge_w` gegen
`current_limit` statt nur der Ereignisse aus dem Log.

**Erledigt (17.08.2026)**: In der Nacht 14.08. drosselte die Box sich für
~40 Minuten (01:41–02:21) eigenmächtig auf 6 A, während unser Limit
durchgehend unverändert bei 16 A stand — bestätigt per Mitschnitt und Log,
aber die Ursache blieb offen, weil die Cloud-Antwort viel mehr Felder liefert
als `wallbox_snapshot()` bis dahin auswertete. `power_sharing_status`,
`grid.status`, `pru.status`, `current_mode`, `preventive_discharge` und
`ecosmart.enabled` landen jetzt mit im Mitschnitt (`wallbox_cloud` in
`recordings/status-*.jsonl`) — bei einer Wiederholung sollte sich daraus
ablesen lassen, ob z. B. Power Sharing/Fuse Protection dahintersteckt (der
Tarif hat `DYNAMIC_POWER_SHARING` im Plan, `ecosmart.enabled` stand zum
Zeitpunkt der Prüfung auf `false`, scheidet also als Ursache aus).

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