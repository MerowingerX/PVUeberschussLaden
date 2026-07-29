# Die Nachtladung sprang nicht an (29.07.2026)

## Beobachtung

Am 29.07. um 00:00 war das Fahrzeug angeschlossen und die Wallbox online. Der
Ladevorgang startete nicht mit verminderter Leistung, sondern gar nicht. Gegen
00:30 musste die Wallbox vom OCPP-Server getrennt werden, damit überhaupt
geladen werden konnte.

Die Web-UI war dabei die ganze Zeit normal bedienbar und zeigte plausible
Werte. Der Versuch, von Hand nachzuhelfen — Umschalten auf „Sofort laden",
Freigabe sperren und wieder erteilen — ließ sich ausführen, hatte auf die
Wallbox aber keinerlei Wirkung.

Ein früherer Fehler (Nachtladung lief mit 6 A statt 16 A,
[issue_limit_to_6A.md](issue_limit_to_6A.md)) war kurz zuvor behoben worden.
Der Verdacht lag deshalb auf einem unsauberen Zusammenspiel zwischen
Nachtfenster und PV-Überschusslogik: dass irgendein Zweig des
Überschussladens die Freigabe wieder aufhebt.

## Was es nicht war

Die Priorisierung im Regeltakt ist genau so, wie sie sein muss. Das
Nachtfenster steht vor der gesamten PV-Logik und steigt mit `return` aus:

```python
# Nachttarif-Fenster oder Modus "fast": volle Leistung
if state.mode == "fast" or in_night_window():
    ...
    else:
        await try_start(now, MAX_AMPS)
    return
```

Kein Zweig des Überschussladens kann von dort aus noch etwas zurücknehmen.
Ebenfalls ausgeschlossen: `night_enabled`, der Freigabe-Zustand, Modbus und
die OCPP-Verbindung — alle vier waren nachweislich in Ordnung.

Der Fehler saß eine Ebene höher. **Der Regeltakt lief seit dem Vortag, 07:35
Uhr, überhaupt nicht mehr.** Von 07:36 am 28.07. bis 00:22 am 29.07. steht im
Log kein einziges `Limit gesetzt`, kein `RemoteStart`, kein Startversuch — knapp
17 Stunden ohne eine einzige Regelaktion.

Um 00:22:18 endet das Fenster, weil die Wallbox von Hand aus dem OCPP-Betrieb
genommen wurde, um überhaupt laden zu können. Alles danach taugt nicht als
Beleg: ohne Verbindung ist `charge_point is None`, und dann schweigt auch ein
gesunder Regler. Die Beweise liegen deshalb sämtlich davor, und keiner von
ihnen hängt an der OCPP-Verbindung — der Mitschnitt schreibt alle 10 s
unabhängig von der Wallbox, der Cloud-Abruf läuft übers Internet, und die
StatusNotifications um 16:16:04 und 00:21:31 belegen selbst, dass die Box in
der stillen Zeit verbunden war.

## Ursachenkette

### 1. Auslöser: Reconnect-Flattern der Wallbox

Am 28.07. zwischen 07:34:31 und 07:36:20 baute die Pulsar fünf Mal die
Verbindung auf und ab, ohne Close-Frame (`no close frame received or sent`).

### 2. Der eigentliche Fehler: `control_task` ohne Schutzschirm

`set_limit`, `remote_start` und `remote_stop` rufen nackt `await self.call(...)`.
Auf einer toten Socket wirft das `ConnectionClosed`, bei einer stummen Box nach
30 s `TimeoutError`. `control_task` hatte kein `try/except` — die Ausnahme flog
durch `control_step` bis in das `asyncio.gather` in `main()` und beendete den
gesamten Dienst. Von allen Daueraufgaben war der Regeltakt die einzige
ungeschützte, und zugleich die einzige, die die Wallbox anspricht.

Beim ersten Ausfall um 07:33:10 steht der Beweis wörtlich im Log:

```
File "/app/poc/charge_loop.py", line 1607, in control_task
File "/app/poc/charge_loop.py", line 1634, in control_step
File "/app/poc/charge_loop.py", line 710, in try_start
File "/app/poc/charge_loop.py", line 677, in apply_limit
File "/app/poc/charge_loop.py", line 965, in set_limit
TimeoutError: Waited 30s for response on [...SetChargingProfile...]
```

Da starb der Prozess sauber, Docker startete ihn 37 Sekunden später neu.

### 3. Warum der zweite Ausfall den Prozess nicht beendete

Zwei Minuten später traf es denselben Pfad erneut — diesmal ohne Traceback im
Log, weil der Prozess nie fertig wurde: `asyncio.run` cancelt nach dem Fehler
alle Aufgaben und ruft dann `loop.shutdown_default_executor()`. Dort wartete es
auf einen Thread des Audi-Connectors, der um 07:33:58 mit
`AuthenticationError` abgestürzt war und in einem `requests`-Aufruf ohne
Timeout hing. Der Shutdown wartete bis zum nächsten Tag.

Ohne Prozessende greift `restart: unless-stopped` nicht. `docker ps` führte den
Container weiterhin als `Up 24 hours`.

### 4. Warum es niemandem auffiel

Der aiohttp-Server und `websockets.serve` laufen in eigenen Aufgaben, die der
Cancel nicht erfasst — die Web-UI blieb bedienbar. `modbus_task` fing die
Cancellation versehentlich mit `except Exception` ab (pymodbus wandelt sie in
eine `ModbusIOException` „Request cancelled outside library" um) und lief
weiter — die Messwerte blieben frisch. StatusNotifications der Wallbox kamen
über den noch offenen Handler weiter herein — der Box-Status sah aktuell aus.

Das erklärt auch, warum die Bedienung wirkungslos blieb: `http_mode` und
`http_release` setzen nur Zustand und protokollieren ihn. Sämtliche Wirkung
Richtung Wallbox läuft ausschließlich über `control_task`.

```
00:13:11 Modus (Web): fast
00:15:08 Modus (Web): minpv
00:18:52 Freigabe (Web): zurückgenommen
00:18:55 Freigabe (Web): erteilt
```

Sieben Zustandswechsel in sieben Minuten, kein einziger Aufruf an die Box.

### Gegenprobe am lebenden Objekt

Am 29.07. um 08:33:40 wurde die Wallbox wieder auf OCPP gestellt, während der
Prozess vom Vortag noch lief. Der Verbindungsaufbau ging vollständig durch —
`Wallbox verbunden`, `Wallbox gebootet`, `Wallbox-Status: Available`, drei
`ChangeConfiguration … Accepted`. Das alles kommt aus dem OCPP-Handler, einer
eigenen Aufgabe des websockets-Servers, die den Cancel überlebt hatte.

Danach: `box_connected: true`, `box_seen_s: 0`, `released: true`, `mode: minpv`,
`grid_w: 4.0`. Weder `charge_point` noch `grid_w` waren None, der Regeltakt war
also fällig — und `on_boot` hatte gerade `limit_known = False` gesetzt, was im
nächsten Takt zwingend ein `apply_limit(MAX_AMPS)` samt `Limit gesetzt: 16.0 A`
ausgelöst hätte.

Von 08:33:46 bis 08:35:27 kam keine einzige Loop-Zeile — rund 20 ausgefallene
Takte. Damit ist der Befund nicht mehr aus Abwesenheit erschlossen, sondern
unter kontrollierten Bedingungen reproduziert: OCPP an, Freigabe an, Messwerte
frisch, Regler stumm. Zur selben Zeit lief `modbus_task` nachweislich weiter
(Reconnect um 08:32:59, danach wieder frische Register) — genau die Asymmetrie,
die den Ausfall getarnt hat.

### 5. Ein zweiter, unabhängiger Fehler

`on_connect` setzte im `finally` pauschal `charge_point = None`. Öffnet die Box
die neue Verbindung, bevor die alte zumacht — im Log am 28.07. mehrfach —,
löschte der Handler der alten Verbindung beim Aufräumen die **neue**. Der
Regeltakt lief danach über `if charge_point is None: continue` blind weiter,
obwohl die Box verbunden war und weiter Statusmeldungen schickte.

Dieser Fehler allein hätte denselben Ausfall erzeugen können.

## Behebung

Drei Ebenen, damit kein einzelner Defekt mehr denselben Effekt haben kann.

**Ein fehlgeschlagener Takt kostet einen Takt.** `control_task` fängt jede
Ausnahme, protokolliert sie, hinterlegt sie für die UI und regelt im nächsten
Takt weiter.

**Eine abgestürzte Aufgabe läuft neu an.** Jede Daueraufgabe hängt jetzt in
`bewacht()` statt nackt im `gather`. Ein Fehler im Mitschnitt kann den Regler
nicht mehr stoppen.

**Bleibt der Takt trotzdem aus, endet der Prozess.** `control_task` setzt in
jeder Runde einen Herzschlag (`state.last_tick`, monotone Uhr). Ein
Wächter-*Thread* — bewusst kein asyncio-Task, der beim Abbruch von `main()`
mitstürbe — beendet den Prozess mit `os._exit(1)`, wenn der Herzschlag länger
als `PVUEB_WATCHDOG_S` (Standard 120 s) ausbleibt. `os._exit` statt `sys.exit`,
weil genau der ordentliche Shutdown-Pfad hier hängen geblieben ist. Was einen
Neustart überleben muss, steht ohnehin in der Sitzungssicherung.

Der Herzschlag steht vor allen Abbruchbedingungen: eine getrennte Wallbox oder
ein toter Modbus sind Betriebszustände, keine Reglerfehler — sonst löste jedes
abgezogene Ladekabel einen Neustart aus.

**Sichtbarkeit.** `tick_age_s` steht in `/api/status`, als Zeile „Regeltakt" auf
der Debug-Seite und im Docker-`HEALTHCHECK`. Ohne diese Zahl sieht ein Dienst,
der nur noch Messwerte anzeigt, genauso gesund aus wie einer, der regelt.

**Reconnect-Race.** `on_connect` räumt nur noch auf, wenn es selbst noch der
aktive Zugang ist (`if charge_point is cp`).

**Audi-Anbindung entfernt.** Sie lieferte reine Anzeigewerte, hing seit Wochen
in einem Anmeldefehler (`invalid assertion headers`), brachte einen eigenen
abstürzenden Hintergrund-Thread mit und war über
`run_in_executor(None, car.fetch_all)` ohne Timeout die Ursache dafür, dass der
Prozess nicht sterben konnte. Ein Anzeigewert rechtfertigt das nicht. Die
myWallbox-Cloud bleibt (Referenzmessung, reines aiohttp mit Timeouts), die
Sonnenprognose bleibt (einzige Cloud-Quelle mit Regelwirkung — sie entscheidet
über die Batterie-Netzladung und fällt ohne Antwort auf „nicht laden" zurück).

PV-Regelung und Nachtfenster brauchen keine Internetverbindung.

## Regressionstests

[poc/test_robust.py](../poc/test_robust.py), mit `make test` zusammen mit den
Simulationsszenarien. Gegen den Stand vor der Behebung schlagen sie fehl —
`test_takt_ueberlebt_wallbox_fehler` mit genau dem Traceback aus dem Log oben,
`test_reconnect_raeumt_nicht_die_neue_verbindung_weg` mit der gelöschten
Verbindung.

| Testfall | Was er festhält |
|---|---|
| `test_takt_ueberlebt_wallbox_fehler` | Eine werfende Wallbox beendet den Regeltakt nicht |
| `test_takt_erholt_sich` | Nach dem Fehler wirkt der nächste Takt wieder |
| `test_herzschlag_ohne_wallbox` | Getrennte Box löst keinen Neustart aus |
| `test_watchdog_entscheidung` | Der Wächter schlägt zur richtigen Zeit an — und vor dem ersten Takt nicht |
| `test_bewacht_startet_neu` | Eine abgestürzte Nebenaufgabe läuft neu an |
| `test_reconnect_raeumt_nicht_die_neue_verbindung_weg` | Die alte Verbindung löscht die neue nicht |

## Nachgezogen: die Rangfolge in den Code

Beim Durchgehen der Funktionen nach Wichtigkeit fiel eine zweite Kopplung auf,
die nichts mit dem Absturz zu tun hatte, aber denselben Effekt haben konnte.

| Funktion | braucht Wallbox | braucht Wechselrichter |
|---|---|---|
| Nachtfenster laden | ja | **nein** |
| PV-Überschussladen | ja | ja |
| Huawei-Akku aus dem Netz | **nein** | ja |

`control_task` setzte den ganzen Takt aus, sobald `state.grid_w` fehlte — und
`modbus_task` setzt genau das beim Verbindungsabbruch. **Ein Modbus-Ausfall
legte damit auch das Nachtladen still**, obwohl der Nachtzweig die Netzleistung
gar nicht anfasst; seine erste Verwendung steht hinter dessen `return`. Die
Wache steht jetzt dort, wo sie hingehört: `charge_point` vor dem gesamten Takt,
`grid_w` vor dem PV-Zweig.

Eine laufende PV-Ladung wird bei Modbus-Ausfall nicht abgebrochen — der
Reconnect dauert 10 s, und ein Stopp wegen eines toten Sensors wäre schlimmer
als ein stehengelassenes Limit. Sichtbar wird der Zustand über `betriebsstufe()`
(`kein Laden` / `eingeschränkt` / `voll`), im Status als `betrieb` und in der UI
als eigene Zeile.

Dazu eine Wache über die OCPP-Leitung selbst (`box_link_task`). Der harmlose
Fall ist der Abriss — den meldet der Handler. Der gefährliche ist die halbtote
Leitung: TCP steht, die Box schweigt. Dann bleibt `charge_point` gesetzt, jeder
Aufruf läuft 30 s in den OCPP-Timeout, und der Wächter über dem Regeltakt würde
den Prozess im Zweiminutentakt neu starten. Schweigt die Box länger als das
Dreifache des Heartbeat-Intervalls (mindestens 45 s), wird die Verbindung
freigegeben und geschlossen; den Neuaufbau macht die Box selbst.

## Lehre

Der Ausfall war nicht, dass etwas kaputtging — das passiert. Der Ausfall war,
dass es 17 Stunden lang niemand merken konnte, weil jede Anzeige, auf die man
in so einem Moment schaut, weiterhin gesunde Werte lieferte. Ein Regler braucht
eine Zahl, die ausschließlich davon abhängt, dass er regelt.
