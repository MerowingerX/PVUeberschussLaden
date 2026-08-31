# what to do if set to "Sofort Laden, mit vollerLeistung"

## The problem

am Freitag, 28.8.2026, wurde nachmittags mit dem Button "Sofort laden - mit voller Leistung" eine Ladung angestossen. Diese wurde ordnungsgemäß durchgeführt, mit 11 KW.

am Sonntag abend dann wieder ein Fahrzeug angestöpselt. Da der button noch auf "sofort laden - mit voller Leistugn" gestanden hat, wurde sofort eine Ladung begonnen, nun aber mit 4,5 KW, also vermutlich 6A Ladestrom.

## der fehler/die erweiterung

1. wenn das Fahrzug bei "sofort laden - mit voller leistung" dann auf "UspendedEV" geht, also die Ladung beendet ist, oder das fahrzeug sogar abgesteckt wird, soll auf "PV Überschuß laden" zurückgewechselt werden. "Sofort Laden" soll also nur einmalig genutzt werden.

2. die Ladeleistung wurde nach dem Ende auf 6A runtergezogen. Als dann das Fahrzeug am Sonntag abend erneut angesteckt wurde, ist die Ladeleistung nicht auf 11 KW erhöht worden.

Eventuell müssen wir einen Automaten für die Ladelestung einführen.

- Sofort laden: 11 KW, wird in jedem Fall ständig übertragen, nciht nur einmalig, villeicht im 1 minuten rythmus?

- PV Überschuß-Laden:
    je nach algorithmus-wert, mit Batteriezuschuß usw.

- kein Laden aktiv: 11 KW/16A als einstellung regelmäßig übertragen

## Aufgabe

analysiere die beobachtung anhand der aufgezeichneten Daten und erabriete erst eine Analyse und dann eine Lösung

PVEub-Fernrechner: 192.168.100.4
---

# Analyse (31.08.2026)

Grundlage: Rohes Container-Log auf 192.168.100.4
(`/var/lib/docker/containers/3460f29…/3460f29…-json.log`, **nicht** `docker logs`)
und der Mitschnitt `recordings/status-2026-08-{28,30,31}.jsonl`.

## Was wirklich passiert ist

### Freitag, 28.08.2026 — die Ladung, die „ordnungsgemäß" lief

```
13:22:25  Anschluss 1 = Preparing                    Fahrzeug angesteckt
13:23:24  Modus (Web): fast                          Knopf gedrückt
13:23:28  Limit gesetzt: 16.0 A                      → TxDefaultProfile, es gibt noch keine Transaktion
13:23:28  RemoteStart: Accepted
13:23:29  Transaktion gestartet (meter 7906218 Wh)
13:23:32  Anschluss 1 = Charging
13:23:43  WARNING lädt mit 0 W, erlaubt 16.0 A — Limit wirkt nicht
13:24:53  charge_w =  4182 W   current_limit = 16   app_max_a = 6     ← 6 A
13:27:53  charge_w =  4188 W   current_limit = 16   app_max_a = 6
13:28:29  Limit gesetzt: 16.0 A                      → Auffrischung nach 300 s, jetzt als TxProfile
13:28:53  charge_w = 11018 W   current_limit = 16   app_max_a = 16    ← 11 kW
14:18:05  Anschluss 1 = SuspendedEV                  Fahrzeug voll
14:51:50  Transaktion beendet (meter 7915685 Wh)     Modus bleibt "fast"
```

Die Ladung lief **die ersten fünf Minuten mit 6 A** und erst danach mit 11 kW.
Das ist bei 88 Minuten Ladung nicht aufgefallen.

### Sonntag, 30.08.2026 — dieselbe Mechanik, nur früher abgebrochen

```
17:06:52  Anschluss 1 = Preparing                    Fahrzeug angesteckt
17:06:57  Limit gesetzt: 16.0 A                      → TxDefaultProfile, wieder vor der Transaktion
17:06:58  RemoteStart: Accepted                      weil der Modus seit Freitag "fast" ist
17:06:59  Transaktion gestartet (meter 7915685 Wh)
17:07:03  WARNING lädt mit 0 W, erlaubt 16.0 A — Limit wirkt nicht
17:07:22  charge_w =  4284 W   current_limit = 16   app_max_a = 6     ← 6 A
17:07:50  Modus (Web): minpv                         Eingriff von Hand
17:07:54  Limit gesetzt: 6.0 A                       minpv regelt, Überschuss nur 1742 W
17:08:44  Freigabe zurückgenommen → RemoteStop
17:08:50  Transaktion beendet (7915807 Wh)           122 Wh in 111 s ≈ 4,0 kW
```

Die Auffrischung, die am Freitag die 11 kW gebracht hat, wäre um **17:11:58**
gekommen — vier Minuten nach dem Eingriff. Deshalb waren am Sonntag nur die
4,5 kW zu sehen.

Dasselbe Muster steht auch in beiden Nachtladungen:
28.08. 00:00:01 Limit 16 A → 4343 W, um 00:05:02 Auffrischung → 11 kW.
31.08. 00:00 ebenso (`app_max_a` springt um 00:05:45 von 6 auf 16).

## Die zwei Ursachen

### 1. „Sofort laden" ist ein Dauerzustand, kein Kommando

`state.mode` kennt nur `minpv` und `fast` und wird ausschließlich über das
Web-UI umgeschaltet (`http_mode`). Nichts setzt ihn zurück — weder das Ende der
Ladung (`SuspendedEV`), noch das Ende der Transaktion, noch das Abstecken.
Von Freitag 13:23 bis Sonntag 17:07 stand er auf `fast`. Der Regler tat genau
das, was in diesem Modus vorgesehen ist: sobald wieder ein Kabel steckte,
`try_start(MAX_AMPS)`.

### 2. Das Limit geht vor dem Ladestart als TxDefaultProfile raus — und die Box verwirft es

`ChargePoint.set_limit()` wählt die Profilart am laufenden Vorgang:

```python
if state.transaction_id is not None:   # TxProfile, stack_level 1
else:                                  # TxDefaultProfile, stack_level 0
```

`try_start()` sendet das Limit **vor** `RemoteStartTransaction` — dort ist
`transaction_id` immer `None`, also geht immer ein TxDefaultProfile raus.
Diese Pulsar Plus (Firmware 6.7.41) quittiert das mit `Accepted`, eröffnet die
Session danach aber mit ihrem eigenen gespeicherten Wert von 6 A. Im Mitschnitt
ist das an `wallbox_cloud.app_max_a` direkt ablesbar: 6 während der ersten
Minuten, 16 ab der ersten Auffrischung im laufenden Vorgang.

Das ist **dieselbe Ursache wie in `docs/issue_limit_to_6A.md`**. Behoben wurde
damals nur die Folge: die Auffrischung alle `PVUEB_LIMIT_REFRESH_S` = 300 s
holt es nach. Die Ursache — ein Profil zu senden, das für die gleich folgende
Transaktion nicht gilt — blieb stehen. `poc/fake_box.py` führt die Eigenheit
seit dem 23.07.2026 unter `profil_vor_transaktion_verfaellt`; kein Test hat sie
je benutzt.

### 3. (Nebenbefund) Die Warnung feuert ins Leere und ist danach verbraucht

`warn_limit_ineffective()` meldet einmal je Ladevorgang (`limit_warned`).
Sie feuert regelmäßig 10 s nach dem Ladestart, wenn die Box noch `0 W` meldet —
in allen drei protokollierten Vorgängen. Genau der Fall, für den sie gebaut
wurde (4,2 kW bei erlaubten 11 kW), kommt danach nie mehr ins Log, weil der
Merker schon steht. Die Warnung hat den Fehler nicht nur nicht gemeldet, sie
hat ihn verdeckt.

## Was **kein** Fehler ist

Ein Automat für die Ladeleistung existiert bereits und tut, was oben gewünscht
wird — `control_step()` sendet in jedem Zweig zyklisch:

| Lage | gesendet | Takt |
|---|---|---|
| keine Freigabe | `MAX_AMPS` | `limit_refresh_s` |
| `fast` oder Nachtfenster | `MAX_AMPS` | `limit_refresh_s` |
| `minpv`, lädt | Algorithmuswert | `adjust_min_interval_s` / `limit_refresh_s` |
| `minpv`, lädt nicht | `MAX_AMPS` | `limit_refresh_s` |

Der Automat ist nicht das Problem. Das Problem ist, dass sein erster Schuss
beim Ladestart als falsche Profilart rausgeht und die nächste Wiederholung erst
300 s später kommt.

---

# Lösung

1. **Sofort-Laden wird einmalig.** Neuer Parameter `PVUEB_FAST_END_S`
   (Vorgabe 60 s). Im Modus `fast` fällt der Regler auf `minpv` zurück, sobald
   das Fahrzeug abgesteckt ist (`Available`, sofort) oder die Ladung so lange
   liegt (`SuspendedEV`/`SuspendedEVSE`/`Finishing`, entprellt). Die Entprellung
   ist nötig: die Box durchläuft `SuspendedEV` auch beim Start, drei Sekunden
   vor `Charging` (28.08. 13:23:29, 30.08. 17:06:59, 31.08. 00:00:04).

2. **Das Limit geht sofort noch einmal raus, wenn die Transaktion steht.**
   `on_start_transaction` erklärt den Merker für ungültig (`limit_known = False`)
   und gibt das Totband frei (`last_adjust = 0`). Der nächste Regeltakt — also
   binnen `PVUEB_POLL_INTERVAL_S` = 5 s statt 300 s — sendet dasselbe Limit
   erneut, diesmal mit `transaction_id`, also als TxProfile. Dieselbe
   Behandlung greift, wenn eine laufende Transaktion aus `MeterValues`
   übernommen wird.

3. **Die Warnung bekommt eine Anlaufzeit.** Neuer Parameter
   `PVUEB_LIMIT_WARN_GRACE_S` (Vorgabe 60 s): erst so lange nach Beginn des
   Ladens wird die Ladeleistung mit dem Limit verglichen. Damit trifft der
   einmalige Schuss den echten Fall statt der Anlaufflanke.

Regressionstests in `poc/test_robust.py`, gegen die Attrappe mit
`profil_vor_transaktion_verfaellt=True`.

---

## Behoben (31.08.2026)

- `fast_beenden()` im Regeltakt: „Sofort laden" fällt auf `minpv` zurück,
  sobald die Ladung erledigt ist. Neuer Parameter `PVUEB_FAST_END_S` (60 s,
  0 = altes Verhalten). Der Auftrag gilt erst als ausgeführt, wenn Strom
  geflossen ist (`fast_geladen`, überlebt den Neustart in der Sitzung) —
  sonst verfiele der Modus, während man mit dem Kabel zum Auto geht.
- `limit_neu_senden()` aus `on_start_transaction` und aus der
  Transaktionsübernahme in `MeterValues`: der Merker wird entwertet und das
  Totband freigegeben, der nächste Takt schickt dasselbe Limit als `TxProfile`
  nach — nach 5 s statt nach 300 s.
- `warn_limit_ineffective()` wartet `PVUEB_LIMIT_WARN_GRACE_S` (60 s) nach
  Ladebeginn, damit die einmalige Warnung nicht an der Anlaufflanke verpufft.
- Web-UI: der Knopf trägt „gilt nur für diese Ladung".
- Regressionstests `test_sofort_laden_ist_einmalig` und
  `test_protokoll_limit_gilt_ab_transaktionsbeginn` in `poc/test_robust.py`.
  Letzterer benutzt endlich die seit dem 23.07.2026 in `poc/fake_box.py`
  hinterlegte Eigenheit `profil_vor_transaktion_verfaellt` und lässt
  `limit_refresh_s` bei 300 s: was er sieht, kann nur vom Transaktionsbeginn
  kommen.

## Offen, bewusst nicht angefasst

- **Log-Flut.** Steht der Regler in `minpv` mit Überschuss, aber ohne Fahrzeug
  an der Dose, schreibt `control_step` die Zeile „Überschuss … starte Ladung
  mit x A" in **jedem** Takt (alle 5 s): `try_start` kehrt bei leerer Dose um,
  bevor `start_retry_at` gesetzt wird, und der Merker vor dem Aufruf greift
  deshalb nie. Das Container-Log steht bei 18 MB. Eigener Vorgang.
- `PVUEB_LIMIT_REFRESH_S` bleibt bei 300 s. Der Vorschlag „1-Minuten-Rhythmus"
  aus dem Issue behandelt das Symptom; mit dem TxProfile beim Transaktionsstart
  ist die Auffrischung wieder das, was sie sein soll — ein Netz gegen stille
  Divergenz, nicht der Weg zum richtigen Ladestrom.
