# Neustart ohne Ladeunterbrechung

**Status: umgesetzt (2026-07-25), Test am Gerät steht aus.**

Ausgangslage: Ein Neustart des Dienstes (Update, Reboot des Pi, Stromausfall)
unterbrach die Ladung zwar nicht — die Box lädt ihre Transaktion autonom
weiter —, aber der Regler stand danach ohne Zustand da:

- `released` war `False`, `mode` fiel auf `pv` zurück
- `transaction_id` war unbekannt, also lief `remote_stop()` ins Leere: der
  Regler konnte die laufende Ladung weder stoppen noch geordnet weiterregeln
- der Zweig „Freigabe fehlt" setzte das Limit auf 16 A — die Ladung lief
  ungeregelt weiter, den Rest aus dem Netz
- das Boost-Tagesbudget und der „einmal pro Nacht"-Merker der Batterieautomatik
  starteten wieder bei null

## Umgesetzt

Der Dienst sichert seinen Laufzeitzustand nach `PVUEB_SESSION_FILE` (Standard:
`RECORD_DIR/session.json`, sonst `poc/.session.json`) — bei jeder Änderung,
direkt nach dem Start und zusätzlich jede Minute. Der Takt ist bewusst
deutlich feiner als die 10-Minuten-Grenze: Der Zeitstempel misst zugleich die
Ausfallzeit, bei 5-Minuten-Takt gälte ein Ausfall von sechs Minuten als elf.

Gesichert werden: `mode`, `released`, `min_amps`, `night_enabled`,
`heartbeat_s`, `charging`, `transaction_id`, `box_status`, `current_limit`,
`boost_used_wh` + `boost_day`, sowie die drei Merker der Batterie-Netzladung.

## Ist die Datei heil? (Integrität)

Format: Zeile 1 `sha256:<hex>`, ab Zeile 2 der JSON-Rumpf, über den die
Prüfsumme gebildet ist. Beim Lesen wird zuerst die Summe verglichen, dann
geparst — eine Datei, die zufällig gültiges JSON ergibt, aber nicht die
zuletzt geschriebene ist, fiele sonst nicht auf. Fehlt die Kopfzeile
(Sicherung aus einer älteren Fassung), gilt die Datei ebenfalls als ungültig.

Geschrieben wird nach `<datei>.tmp`, dann `fsync` auf die Datei, dann
`os.replace`, dann `fsync` auf das **Verzeichnis**. Ohne die beiden `fsync`
wäre nach einem Stromausfall der Rename sichtbar, der Inhalt dahinter aber
nicht — gelesen würde die vorige Fassung. Genau der Fall, der hier nicht
auftreten darf.

## Ist die Datei aktuell? (Alter)

Die Wanduhr allein taugt dafür nicht: Der Pi hat keine gepufferte Uhr, nach
einem Stromausfall startet er mit der von fake-hwclock gemerkten Zeit und
springt erst beim ersten NTP-Kontakt auf die Wirklichkeit. Deshalb wandern
Boot-Kennung (`/proc/sys/kernel/random/boot_id`) und Systemlaufzeit
(`/proc/uptime`) mit in die Sicherung. Drei Fälle:

Ob die Uhr überhaupt etwas taugt, sagt der Kernel selbst: `adjtimex(2)` mit
genulltem Puffer ist ein reiner Lesezugriff und meldet mit `TIME_ERROR` (5),
dass `STA_UNSYNC` gesetzt ist — die Uhr läuft frei. Das funktioniert auch im
Container, weil die Systemzeit dem Kernel des Hosts gehört; `timedatectl`
schiede genau deshalb aus, es setzt systemd im Container voraus. Der Befund
wandert als `clock_synced` in jede Sicherung.

| Lage | Messung | Warum |
|---|---|---|
| gleiche Boot-Kennung | Differenz der Systemlaufzeiten | exakt und von der Uhr unabhängig — der Normalfall (Dienst-/Container-Neustart) |
| Reboot, NTP beim Sichern **und** jetzt | Wanduhr | beide Zeitstempel stammen von einer disziplinierten Uhr, die Dauer ist echt — auch über einen Stromausfall |
| Reboot, Uhr ohne NTP | `max(Wanduhr, aktuelle Systemlaufzeit)` | seit dem Boot ist mindestens die Laufzeit vergangen, der Ausfall begann davor |
| kein `/proc` | Wanduhr | Rückfall, etwa außerhalb von Linux |

Damit der zweite Fall auch nach einem Stromausfall greift, wartet der Start
bis zu 15 Sekunden auf die NTP-Synchronisation, bevor er die Sicherung
bewertet — aber nur, wenn eine Sicherung existiert und die Uhr tatsächlich
frei läuft. Im Normalbetrieb kostet das nichts.

Ein Zeitstempel aus der Zukunft (mehr als 5 s) heißt: die Uhr ist gesprungen,
das Alter sagt nichts mehr — verworfen. Im Container kommen Boot-Kennung und
Laufzeit vom Host, ein Container-Neustart zählt damit richtig als derselbe
Boot.

`/info` zeigt unter „Sicherung jetzt" beide Prüfungen live — heil oder nicht,
Alter, Messverfahren und Grenze — und daneben den Zustand der Systemuhr.

### Restrisiko

Bleibt nur, wenn der Pi nach einem Stromausfall **kein** NTP erreicht (kein
Netz, Router noch nicht oben) und zugleich rebootet hat: Dann ist die echte
Ausfalldauer prinzipiell nicht messbar, die Untergrenze aus der Systemlaufzeit
ist klein und eine alte Sitzung gälte als frisch. Die Folgen sind begrenzt:
Der Regler übernimmt eine Transaktions-ID, die es nicht mehr gibt, bekommt auf
`RemoteStop` ein `Rejected` und verwirft den Merker; parallel korrigiert die
erste `StatusNotification` der Box den Ladezustand.

Beim Start liest der Dienst die Sicherung, **nachdem** die .env-Konfiguration
steht — die Sitzung darf Laufzeitwerte überschreiben, keine Parameter:

- **jünger als 10 Minuten** → alles übernehmen. Läuft eine Ladung, greift der
  Regler sie mit ihrer Transaktions-ID wieder ab. `limit_known` wird bewusst
  auf `False` gesetzt: was in der Box steht, kann sich geändert haben, also
  geht das Limit im ersten Regeltakt neu raus.
- **älter** → verwerfen, die Sitzung gilt als beendet.
- **fehlt oder ist kaputt** → Vorgabe-Start. Beides landet als Klartext in
  `session_note` und damit im Log, im Debug-Slide und auf `/info`.

Startverhalten ohne gültige Sicherung: **freigegeben, `minpv` mit 6 A**. Die
Anlage soll nach einem Ausfall von allein weiterarbeiten und nicht auf einen
Knopfdruck warten. Wer das nicht will, nimmt die Freigabe im Web-UI zurück —
sie überlebt dann über die Sicherung.

## Zweiter Rückweg: MeterValues

Fehlt die Sicherung oder ist sie zu alt, während die Box weiterlädt, wäre der
Regler wieder ohne Transaktions-ID. Die Box schickt sie aber in jedem
`MeterValues`-Paket mit; der Handler übernimmt sie und setzt `charging`.
Umgekehrt gilt: Antwortet die Box auf `RemoteStop` mit `Rejected`, kennt sie
die Transaktion nicht mehr — der Merker wird verworfen, statt den Stopp im
Regeltakt endlos zu wiederholen.

## Infoseite

`/info` zeigt Commit, Beschreibung (`git describe`), Herkunft, Bauzeit,
Startzeit, Laufzeit, den Sitzungsbefund, den Pfad der Sicherung sowie Ladung,
Transaktion, Modus und Freigabe. Im Container gibt es kein Git-Verzeichnis,
deshalb wandert der Commit als Build-Argument ins Image (`make up`); ohne das
steht dort „unbekannt" statt einer falschen Zahl. Der Debug-Slide verlinkt die
Seite über die Version.

## Grenzen

- Zwischen Prozessende und erstem Regeltakt nach dem Start regelt niemand. Die
  Box behält so lange ihr zuletzt gesetztes Limit — das ist der gewünschte
  Zustand, nicht 16 A.
- Ein Ausfall über 10 Minuten führt zum Vorgabe-Start. Läuft die Ladung dann
  noch, fängt sie der MeterValues-Weg wieder ein.
- Die Sicherung enthält keine Zugangsdaten, aber den Laufzeitzustand — sie
  gehört nicht ins Repository (`.gitignore`).
