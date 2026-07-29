# Benachrichtigung

**Status: entworfen (2026-07-29), nicht umgesetzt.**

Ausgangslage: Am 28./29.07. regelte PVueb 17 Stunden lang nicht mehr, ohne dass
es auffiel — die Web-UI war bedienbar, die Messwerte frisch, nur der Regeltakt
war weg (`docs/issue_nightly_load_did_not_work.md`). Der Wächter-Thread macht
daraus jetzt höchstens zwei Minuten. **Er sagt es aber niemandem.** Steht morgens
ein leeres Auto in der Einfahrt, ist die Frage nicht mehr, ob der Regler lebte,
sondern warum niemand Bescheid gesagt hat.

Das Ziel ist deshalb nicht mehr Überwachung, sondern eine Meldung. Und zwar über
das **Ergebnis**, nicht über den Mechanismus: nicht „Task abgestürzt", sondern
„das Auto lädt nicht, obwohl es sollte".

## Zwei Kanäle, zwei Aufgaben

|  | beantwortet | wer meldet |
|---|---|---|
| **ausgehend** | „Ich lebe, und etwas stimmt nicht" | PVueb selbst |
| **Totmannschalter** | „Ich lebe nicht mehr" | ein Dienst außerhalb des Pi |

Die Trennung ist keine Bequemlichkeit, sondern Logik: Eine Meldung, die der
sterbende Prozess selbst verschicken soll, ist genau dann weg, wenn sie gebraucht
wird. Das war der 28.07.

## Ausgehend: eine kanalunabhängige Meldestelle

PVueb kennt **keinen** Messenger. Es schickt ein JSON an eine konfigurierbare
Adresse und vergisst es:

```
PVUEB_NOTIFY_URL=http://127.0.0.1:8090/notify     leer = aus
PVUEB_NOTIFY_TIMEOUT_S=5
```

```json
{"quelle": "pvueb", "thema": "nachtladung",
 "text": "Nachtfenster aktiv, Fahrzeug steckt, seit 15 min keine Ladung",
 "stufe": "alarm",
 "schluessel": "nachtladung-2026-07-29", "wiederholsperre_s": 3600}
```

Heute zeigt die Adresse auf ntfy und tut sofort etwas, später auf
`myhome-messenger` (eigenes Projekt, siehe `feature_MyHomeMessenger.md`) — ohne
dass sich in PVueb eine Zeile ändert. Leer heißt aus, wie bei allen optionalen
Anbindungen hier.

`schluessel` und `wiederholsperre_s` reist PVueb nur mit; **entprellt wird beim
Empfänger.** Sonst müsste jedes meldende Projekt dieselbe Logik nachbauen, und
eine flatternde Wallbox schickt nachts zweihundert Nachrichten. Danach ist der
Kanal stummgeschaltet und die eine Meldung, die zählt, kommt nicht durch.

`stufe`: `info` | `warnung` | `alarm`. Nur `alarm` darf nachts klingeln.

### Regeln für die Umsetzung

Aus dem Ausfall, der dieses Feature ausgelöst hat, folgen drei harte Vorgaben:

- **Wegwerfversuch.** Der Regeltakt legt eine Nachricht in eine Warteschlange
  und läuft weiter. Kein `await` auf den Versand, keine Wiederholung im
  Regelpfad. Ein toter Messenger darf niemals eine Ladung beeinflussen —
  dieselbe Regel wie für die myWallbox-Cloud.
- **Eigene überwachte Aufgabe** unter `bewacht()`, wie jede andere Daueraufgabe.
- **Nie ein Fehler nach oben.** Versandfehler landen im Log und in einem
  Statusfeld, sonst nirgends.

## Was gemeldet wird

Absichtlich wenige Regeln. Jede beschreibt einen Zustand, den der Betreiber
kennen muss, und keine beschreibt einen Programmierfehler.

| Regel | Bedingung | Stufe |
|---|---|---|
| **Nachtladung läuft nicht an** | Nachtfenster aktiv, Freigabe erteilt, Fahrzeug steckt (`box_status` `Preparing`/`SuspendedEV`), nach 15 min `charging: false` | alarm |
| **Wallbox weg** | `box_connected: false` länger als 30 min, während Freigabe erteilt ist | warnung |
| **Betriebsstufe gefallen** | `betrieb` wechselt auf `eingeschränkt` oder `kein Laden` und bleibt 15 min dort | warnung |
| **Regeltakt gestolpert** | `tick_error` gesetzt und über 5 min nicht verschwunden | warnung |
| **Prozess neu gestartet** | Start nach einem Wächter-Abbruch (Ausfallzeit aus der Sitzungssicherung) | warnung |
| **Limit wirkt nicht** | `limit_effective: false` bei laufender Ladung (Fall `issue_limit_to_6A.md`) | warnung |
| **Wallbox-Rückfallebene nicht gesetzt** | abendliche Umstellung der Box fehlgeschlagen (siehe `feature_WallboxRueckfallebene.md`) | alarm |
| **Tagesbericht** | einmal morgens: geladene kWh, Netzanteil, Neustarts, längster Taktausfall | info |

Die erste Regel ist die wichtigste, und sie prüft bewusst **nicht**, ob die
Software lebt. Sie hätte die Nacht vom 29.07. erwischt — und genauso eine
Wallbox, die nicht hereinkommt, ein Auto mit eigenem Ladetimer oder ein Profil,
das die Box ignoriert. Alles Fälle, bei denen der Regler tadellos tickt und das
Auto trotzdem leer bleibt.

## Totmannschalter

PVueb ruft im Regeltakt eine Adresse auf, die ein Dienst **außerhalb des Pi**
überwacht. Bleiben die Aufrufe aus, meldet sich dieser Dienst.

```
PVUEB_HEARTBEAT_URL=https://hc-ping.com/<uuid>    leer = aus
PVUEB_HEARTBEAT_INTERVAL_S=60
```

Aufgerufen wird nur, wenn der Takt wirklich lief — derselbe `state.last_tick`,
den der Wächter liest. Ein Ping, der auch bei stehendem Regler rausgeht, wäre
schlimmer als keiner: er bescheinigt Gesundheit, die nicht da ist.

`healthchecks.io` macht genau das und ist selbst hostbar. Wichtig ist nur, dass
der Dienst **nicht auf demselben Pi** läuft, sonst überwacht er seinen eigenen
Tod.

Damit ist die Abdeckung vollständig, soweit sie erreichbar ist:

| Fall | wer meldet |
|---|---|
| Regeltakt stolpert | PVueb, ausgehend |
| Regeltakt steht | Wächter startet neu, PVueb meldet den Neustart |
| Prozess in Neustartschleife | Totmannschalter (Pings bleiben aus) |
| Pi tot, Strom weg, SD-Karte defekt | Totmannschalter |
| Internet weg | niemand — Meldungen laufen auf, gehen nach Rückkehr raus |

Der letzte Fall bleibt offen und ist hinzunehmen: Ohne Netz gibt es keinen Weg
nach draußen. Die Spool-Datei im Messenger sorgt dafür, dass die Meldungen
nachträglich ankommen statt verloren zu gehen.

## Was dieses Feature nicht ist

Keine Fernsteuerung. Befehle vom Telefon zurück in die Anlage sind Sache des
Messengers und laufen dort gegen eine Erlaubnisliste
(`feature_MyHomeMessenger.md`). PVueb bekommt dadurch keinen zweiten Steuerweg
neben der Web-UI.

## Tests

- Versand-Endpunkt nicht erreichbar → Regeltakt unbeeinflusst, kein `tick_error`
- Versand-Endpunkt hängt (antwortet nie) → Takt läuft weiter, Timeout greift
- Bedingung „Nachtladung läuft nicht an" wird bei steckendem, nicht ladendem
  Fahrzeug genau einmal ausgelöst, nicht je Takt
- Heartbeat geht **nicht** raus, wenn `state.last_tick` veraltet ist
