# myhome-messenger — Skizze für ein eigenes Projekt

**Status: Entwurf (2026-07-29). Eigenes Repository, eigener Container auf dem Pi.
Gehört nicht zu PVueb — liegt hier nur, weil die Anforderung hier entstanden ist.**

PVueb braucht einen Weg, den Betreiber zu erreichen
(`feature_Benachrichtigung.md`). Es ist nicht das einzige Projekt auf dem Pi mit
diesem Bedarf, und gewünscht ist außerdem ein Rückkanal: eine „myHome"-Identität,
über die Serverprozesse mit dem Telefon sprechen und Befehle entgegennehmen.

Das ist genug eigener Gegenstand für ein eigenes Projekt. PVueb kennt davon
nichts außer einer URL.

## Warum ein Dienst und keine Bibliothek

Eine gemeinsame Bibliothek hieße: jedes Projekt kennt den Bot-Token, jedes muss
Wiederholungen, Ratenbegrenzung und Entprellung selbst können, und ein
Kanalwechsel fasst alle Projekte an. Ein Dienst zieht die Grenze an genau einer
Stelle:

```
PVueb ─┐
Astro ─┼─► POST 127.0.0.1:8090/notify ─► myhome-messenger ─► Telegram
sonst ─┘                                        ▲
                                                └─ Befehle zurück an registrierte Endpunkte
```

Erzeuger bleiben dumm: JSON abschicken, vergessen. Alles Schwierige — Entprellen,
Ruhezeiten, Ratenbegrenzung, Wiederholung, Kanalwahl — sitzt an einer Stelle.

## Kanalwahl

**Telegram-Bot** als erster Kanal.

| Kanal | Rückkanal | Aufwand | Bewertung |
|---|---|---|---|
| **Telegram-Bot** | ja, nativ (Befehle, Buttons, Themen je Projekt) | HTTP-POST zum Senden, Long-Poll zum Empfangen | **Empfehlung** |
| Signal | ja, Ende-zu-Ende | `signal-cli` im Container, Mobilnummer registrieren | höchster Wartungsaufwand |
| Discord | ja | Bot, Kanäle je Projekt | gleichwertig, Handy-Benachrichtigungen gehen leichter unter |
| ntfy | eingeschränkt (Aktions-Buttons) | curl, selbst hostbar | perfekt zum Senden, kein Gespräch |
| WhatsApp | ja | Business-API, Nummer registrieren, Template-Freigaben, sperrbares Konto | unverhältnismäßig |

Telegram deckt den Fall am besten ab: eine Gruppe „myHome" mit einem Thema je
Projekt, Benachrichtigungen je Thema stummschaltbar, Befehle mit
Autovervollständigung, und der Bot braucht keine Telefonnummer.

Die vorhandene Mobilnummer ist damit nicht verschenkt — sie ist das, was
**Signal** braucht. In Reserve halten, nicht damit anfangen: `signal-cli` bricht
regelmäßig an Protokolländerungen, und die Wartung landet beim Betreiber. Wenn
später Ende-zu-Ende wichtig wird, ist der Umstieg genau ein Modul im Broker —
das ist der Sinn der Trennung.

## Schnittstelle nach innen

```
POST /notify
{"quelle": "pvueb", "thema": "nachtladung",
 "text": "Nachtfenster aktiv, Fahrzeug steckt, seit 15 min keine Ladung",
 "stufe": "alarm",
 "schluessel": "nachtladung-2026-07-29", "wiederholsperre_s": 3600}
```

| Feld | Bedeutung |
|---|---|
| `quelle` | Projekt. Bestimmt das Telegram-Thema und die Erlaubnisliste für Befehle |
| `thema` | Unterteilung innerhalb des Projekts, optional |
| `stufe` | `info` \| `warnung` \| `alarm`. Nur `alarm` darf in der Ruhezeit klingeln |
| `schluessel` | Identität des Sachverhalts. Gleicher Schlüssel innerhalb der Sperre = keine zweite Nachricht |
| `wiederholsperre_s` | wie lange dieser Schlüssel stumm bleibt |

Antwort sofort `202`, sobald die Nachricht auf Platte liegt. Der Erzeuger wartet
nie auf den Versand.

**Spool auf Platte**: Nachricht landet zuerst als Datei, dann geht sie raus.
Damit überlebt eine Meldung einen Neustart des Messengers und einen
Internetausfall — sie kommt verspätet an statt gar nicht.

## Rückkanal

```
/pvueb status
/pvueb freigabe an
```

Der Broker leitet an einen je Projekt registrierten Endpunkt weiter, gegen eine
**Erlaubnisliste erlaubter Befehle**. Kein Durchreichen beliebiger Zeichenketten.

Für PVueb heißt das ausdrücklich: dadurch entsteht **kein zweiter Steuerweg**
neben der Web-UI. Der Broker ruft dieselbe HTTP-Schnittstelle auf, die das
Web-UI auch benutzt.

## Sicherheit

Das ist der Teil, der gern übersprungen wird. Ein Kanal, über den sich Dinge im
Haus auslösen lassen, ist ein Fernzugang.

- **Eingang bindet auf `127.0.0.1` bzw. das Docker-Netz, nie auf `0.0.0.0`.**
  Dieselbe Überlegung steht schon als Kommentar in PVuebs `docker-compose.yml`:
  Docker umgeht ufw-Regeln, die Bindung ist die Verteidigung.
- **Erlaubnisliste auf genau eine Telegram-User-ID.** Alles andere kommentarlos
  verwerfen. Ein Bot ist für jeden ansprechbar, der seinen Namen kennt.
- **Der Bot-Token liegt nur im Messenger**, nicht in den meldenden Projekten. Das
  ist der zweite Grund für den Dienst statt der Bibliothek.
- **Kein Geheimnis ins Log.** Token weder beim Start noch im Fehlerfall
  ausgeben.
- **Rückkanal nur lesend beginnen.** `status`, `logs`, `letzte fehler`.
  Schreibende Befehle erst, wenn der lesende Betrieb steht.

## Betriebsverhalten

- **Ratenbegrenzung** je Quelle, hart. Eine flatternde Wallbox darf nicht 200
  Nachrichten erzeugen; danach ist der Kanal stummgeschaltet und die eine
  Meldung, die zählt, kommt nicht durch.
- **Ruhezeiten**: `info` und `warnung` werden nachts gesammelt und morgens
  zugestellt, `alarm` geht sofort durch.
- **Sammelmeldung** statt Einzelnachrichten, wenn mehrere Meldungen derselben
  Quelle in kurzem Abstand auflaufen.

## Was der Messenger nicht kann

**Er kann den Tod des Pi nicht melden** — er stirbt mit. Das ist keine
Konfigurationsfrage, sondern Logik. Dafür bleibt der Totmannschalter außerhalb
zuständig (`feature_Benachrichtigung.md`). Der Messenger braucht denselben
Schutz für sich selbst: auch er sollte extern gepingt werden.

## Reihenfolge

1. Senden: `/notify`, Spool, Telegram, Entprellung, Ratenbegrenzung. Damit ist
   der Zweck für PVueb erfüllt.
2. Empfangen: Long-Poll, Erlaubnisliste, lesende Befehle.
3. Schreibende Befehle, je Projekt einzeln freigeschaltet.

PVueb braucht nur Schritt 1 — und selbst den nicht zwingend, weil
`PVUEB_NOTIFY_URL` genauso auf ntfy zeigen kann, bis der Messenger steht.
