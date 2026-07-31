# Benachrichtigung

**Status: ausgehender Weg umgesetzt (2026-07-29). Totmannschalter offen.**

Ausgangslage: Am 28./29.07. regelte PVueb 17 Stunden lang nicht mehr, ohne dass
es auffiel — die Web-UI war bedienbar, die Messwerte frisch, nur der Regeltakt
war weg (`docs/issue_nightly_load_did_not_work.md`). Der Wächter-Thread macht
daraus jetzt höchstens zwei Minuten. **Er sagte es aber niemandem.** Steht
morgens ein leeres Auto in der Einfahrt, ist die Frage nicht mehr, ob der Regler
lebte, sondern warum niemand Bescheid gesagt hat.

Das Ziel ist deshalb nicht mehr Überwachung, sondern eine Meldung. Und zwar über
das **Ergebnis**, nicht über den Mechanismus: nicht „Task abgestürzt", sondern
„das Auto lädt nicht, obwohl es sollte".

## Zwei Kanäle, zwei Aufgaben

|  | beantwortet | wer meldet | Stand |
|---|---|---|---|
| **ausgehend** | „Ich lebe, und etwas stimmt nicht" | PVueb selbst | umgesetzt |
| **Totmannschalter** | „Ich lebe nicht mehr" | ein Dienst außerhalb des Pi | offen |

Die Trennung ist keine Bequemlichkeit, sondern Logik: Eine Meldung, die der
sterbende Prozess selbst verschicken soll, ist genau dann weg, wenn sie gebraucht
wird. Das war der 28.07.

## Ausgehend: eine kanalunabhängige Meldestelle

PVueb kennt **keinen** Messenger. Es schickt ein JSON an eine konfigurierbare
Adresse und vergisst es:

```
PVUEB_NOTIFY_URL=http://myhome-messenger:8090/notify    leer = aus
PVUEB_NOTIFY_TIMEOUT_S=5
PVUEB_MELDE_OFFLINE_S=300
```

```json
{"quelle": "pvueb", "thema": "wallbox",
 "text": "Wallbox ist nicht mehr verbunden (OCPP). Es kann nicht geladen werden, auch nicht im Nachtfenster. Seit 7 min.",
 "stufe": "alarm",
 "schluessel": "wallbox-alarm", "wiederholsperre_s": 3600}
```

Am anderen Ende hängt `myhome-messenger` (eigenes Repository, eigener
Container). Der Kanal dort ist **ntfy**; die Adresse könnte genauso direkt auf
ntfy zeigen. Leer heißt aus, wie bei allen optionalen Anbindungen hier.

`schluessel` und `wiederholsperre_s` reist PVueb nur mit; **entprellt wird beim
Empfänger.** Sonst müsste jedes meldende Projekt dieselbe Logik nachbauen, und
eine flatternde Wallbox schickt nachts zweihundert Nachrichten. Danach ist der
Kanal stummgeschaltet und die eine Meldung, die zählt, kommt nicht durch.

`stufe`: `info` | `warnung` | `alarm`. Nur `alarm` darf nachts klingeln.

### Regeln für die Umsetzung

Aus dem Ausfall, der dieses Feature ausgelöst hat, folgen drei harte Vorgaben.
Alle drei sind eingehalten:

- **Wegwerfversuch.** `melden()` legt in eine Warteschlange und kehrt sofort
  zurück. Kein `await` auf den Versand, keine Wiederholung im Regelpfad. Ein
  toter Messenger darf niemals eine Ladung beeinflussen — dieselbe Regel wie für
  die myWallbox-Cloud. Die Warteschlange ist auf `NOTIFY_QUEUE_MAX` begrenzt und
  wirft Ältestes weg; unbegrenzt wäre sie ein Speicherleck mit Anlauf.
- **Eigene überwachte Aufgaben** unter `bewacht()`: `notify_task` stellt zu,
  `melde_task` prüft die Regeln. Beide laufen im eigenen Takt, nicht im
  Regeltakt.
- **Nie ein Fehler nach oben.** Versandfehler landen im Log und in
  `notify_error`, sonst nirgends.

### Warum ein Beobachter und kein Aufruf im Regelpfad

Die Regeln sitzen in `Melder.pruefen()` — einer Klasse, die nur liest. Sie kann
nichts stoppen, verzögern oder abbrechen, und sie steht an einer Stelle statt
über zehn Handler verstreut. Deshalb lässt sie sich in `poc/test_melden.py` ohne
Wallbox, ohne Wechselrichter und ohne Uhr durchspielen (37 Prüfungen).

## Was gemeldet wird

Absichtlich wenige Regeln. Jede beschreibt einen Zustand, den der Betreiber
kennen muss, und keine beschreibt einen Programmierfehler.

| Regel | Bedingung | Stufe |
|---|---|---|
| **Prozess neu gestartet** | jeder Start, mit Ausfallzeit aus der Sitzungssicherung | alarm |
| **Wechselrichter weg** | kein erfolgreicher Modbus-Poll seit `PVUEB_MELDE_OFFLINE_S` | alarm |
| **Wallbox weg** | keine OCPP-Verbindung seit `PVUEB_MELDE_OFFLINE_S` | alarm |
| **… wieder da** | jeweils die Rückkehr | info |
| **Fahrzeug** | `box_status` wechselt zwischen „steckt" (`Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`) und allem anderen | info |
| **Nachtladen** | `charging` wechselt, während das Nachtfenster gilt | info |
| **PV-Laden** | `charging` wechselt außerhalb des Nachtfensters | info |
| **Hausakku** | `battery_grid_charge` wechselt | info |

Vier Eigenheiten, die aus dem Betrieb folgen und in den Tests festgehalten sind:

**Die Entwarnung ist eigenständig.** Ohne sie steht ein Alarm im Chat, und
niemand weiß, ob er noch gilt.

**`Preparing` zählt nicht als Fahrzeug.** In diesem Zustand steht die Box auch
dann, wenn sie auf ein Kabel *wartet* — nach einem RemoteStart, einem RFID-Halt
oder einem Start aus der Hersteller-App. In der Nacht zum 30.07.2026 kam so alle
130 s ein Paar „angesteckt / abgesteckt", ohne dass ein Auto in der Einfahrt
stand: der Regler startete in die leere Dose, die Box ging in `Preparing`, fiel
nach ihrem `ConnectionTimeOut` (Werk: 120 s) auf `Available` zurück, der Regler
startete erneut. Die Verweildauer von 120 s konnte das nicht abfangen — sie war
zufällig genauso lang wie der Timeout der Box. Der Regler startet seitdem nicht
mehr in eine leere Dose, und gemeldet wird erst, wenn Strom fließen könnte.
Preis: ein Fahrzeug, das nie über `Preparing` hinauskommt (gesperrte Box,
fehlende Autorisierung), erzeugt keine Meldung. Der bessere Fehler — die
Gegenrichtung meldete ein Auto, das gar nicht da war.

**Eine Nachtladung, die um 08:05 endet, meldet „Nachtladen beendet"** — nicht
„PV-Laden beendet". Der Melder merkt sich die Art, mit der die Ladung begann,
sonst stünde im Chat ein Ende ohne Anfang.

**Schonzeit nach dem Start**, in derselben Länge wie die Ausfallfrist.
Unmittelbar nach einem Neustart ist noch nichts verbunden; ohne die Frist meldete
jeder Neustart beide Geräte als ausgefallen, bevor sie eine Chance hatten.

### Was noch nicht gemeldet wird

Der ursprüngliche Entwurf nannte weitere Regeln. Sie sind **nicht** umgesetzt und
stehen hier, damit die Lücke sichtbar bleibt:

| Regel | Bedingung | warum sie fehlt |
|---|---|---|
| **Nachtladung läuft nicht an** | Nachtfenster aktiv, Freigabe erteilt, Fahrzeug steckt, nach 15 min `charging: false` | die wichtigste Regel des Entwurfs — sie hätte die Nacht vom 29.07. erwischt |
| **Betriebsstufe gefallen** | `betrieb` auf `eingeschränkt`/`kein Laden` und bleibt 15 min dort | teilweise durch „Wallbox weg" abgedeckt |
| **Regeltakt gestolpert** | `tick_error` gesetzt und über 5 min nicht verschwunden | |
| **Limit wirkt nicht** | `limit_effective: false` bei laufender Ladung (`docs/issue_limit_to_6A.md`) | |
| **Wallbox-Rückfallebene nicht gesetzt** | abendliche Umstellung fehlgeschlagen (`feature_WallboxRueckfallebene.md`) | das Feature selbst ist noch nicht umgesetzt |
| **Tagesbericht** | morgens: geladene kWh, Netzanteil, Neustarts, längster Taktausfall | |

Die erste Zeile ist die wichtigste und die unangenehmste. Sie prüft bewusst
**nicht**, ob die Software lebt — sie hätte auch eine Wallbox erwischt, die nicht
hereinkommt, ein Auto mit eigenem Ladetimer oder ein Profil, das die Box
ignoriert. Alles Fälle, bei denen der Regler tadellos tickt und das Auto trotzdem
leer bleibt. Was heute gemeldet wird, deckt diese Fälle nicht ab.

## Totmannschalter

**Noch nicht umgesetzt.** PVueb soll im Regeltakt eine Adresse aufrufen, die ein
Dienst **außerhalb des Pi** überwacht. Bleiben die Aufrufe aus, meldet sich dieser
Dienst.

```
PVUEB_HEARTBEAT_URL=https://hc-ping.com/<uuid>    leer = aus
PVUEB_HEARTBEAT_INTERVAL_S=60
```

Aufgerufen werden darf nur, wenn der Takt wirklich lief — derselbe
`state.last_tick`, den der Wächter liest. Ein Ping, der auch bei stehendem Regler
rausgeht, wäre schlimmer als keiner: er bescheinigt Gesundheit, die nicht da ist.

`healthchecks.io` macht genau das und ist selbst hostbar. Wichtig ist nur, dass
der Dienst **nicht auf demselben Pi** läuft, sonst überwacht er seinen eigenen
Tod.

Damit ist die Abdeckung, soweit sie erreichbar ist:

| Fall | wer meldet | Stand |
|---|---|---|
| Wechselrichter oder Wallbox weg | PVueb, ausgehend | umgesetzt |
| Prozess neu gestartet | PVueb, ausgehend | umgesetzt |
| Regeltakt steht | Wächter startet neu, PVueb meldet den Neustart | umgesetzt |
| Regeltakt stolpert, ohne zu stehen | niemand | offen |
| Prozess in Neustartschleife | Totmannschalter (Pings bleiben aus) | offen |
| Pi tot, Strom weg, SD-Karte defekt | Totmannschalter | offen |
| Internet weg | niemand — Meldungen laufen auf, gehen nach Rückkehr raus | so gewollt |

Der letzte Fall bleibt offen und ist hinzunehmen: Ohne Netz gibt es keinen Weg
nach draußen. Die Spool-Datei im Messenger sorgt dafür, dass die Meldungen
nachträglich ankommen statt verloren zu gehen.

## Was dieses Feature nicht ist

Keine Fernsteuerung. Über ntfy gibt es keinen Rückkanal. Der Messenger hat einen
gebaut (für Signal), er ist nicht in Betrieb und läuft gegen eine Erlaubnisliste;
schreibende Befehle sind dort im Code gesperrt. PVueb bekommt dadurch keinen
zweiten Steuerweg neben der Web-UI.

## Tests

`poc/test_melden.py`, 50 Prüfungen. Abgedeckt:

- erster Durchgang meldet nichts, sondern lernt die Lage (sonst meldete jeder
  Neustart „Fahrzeug angesteckt" für etwas, das längst galt)
- eine leere Dose, die zwischen `Preparing` und `Available` pendelt, erzeugt
  keine einzige Meldung — auch ohne Verweildauer
- Schonzeit nach dem Start, dann Frist, dann Alarm
- ein Ausfall meldet genau einmal, die Rückkehr ebenfalls, ein zweiter Ausfall
  wieder
- Nachtladen behält seine Art über das Fensterende hinaus
- die Warteschlange wächst nicht über ihre Grenze und zählt Verworfenes

Nicht abgedeckt, weil es Netz braucht: der Zustellpfad selbst
(`notify_zustellen`). Er ist gegen den laufenden Messenger von Hand geprüft.
