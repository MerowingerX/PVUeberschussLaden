# Wallbox-Rückfallebene bei totem Pi

**Status: offen — hängt an einer Messung, die noch aussteht. Kein Code.**

Ausgangslage: Alle Schutzebenen aus `docs/issue_nightly_load_did_not_work.md`
sitzen **im** Prozess auf dem Pi. Ist der Pi tot — Netzteil, SD-Karte,
Stromausfall —, hilft keine davon. Ein über Nacht angestecktes Fahrzeug wird am
nächsten Tag gebraucht; genau dafür braucht es eine Ebene, die ohne den Pi
funktioniert.

## Der Denkfehler, der zuerst auszuräumen war

Naheliegend wäre, der Box per OCPP ein wiederkehrendes Ladeprofil
(`chargingProfileKind: Recurring`, `recurrencyKind: Daily`) für 00:00–08:00 mit
16 A zu schicken. **Das hilft nicht.** Ein Ladeprofil begrenzt die Leistung
*innerhalb* einer laufenden Transaktion. Es kann keine starten.

Gestartet wird bei uns per `RemoteStart`, und der kommt vom Pi. Pi tot → kein
`RemoteStart` → keine Transaktion → kein Laden, völlig unabhängig davon, was für
ein Profil in der Box liegt.

Die entscheidende Frage ist deshalb eine andere:

> **Was tut die Pulsar Plus, wenn der OCPP-Server unerreichbar ist?**

## Messung zuerst

Drei mögliche Verhalten, und nur zwei davon lassen eine Rückfallebene zu:

1. **Sie wartet ewig** auf Autorisierung → dieses Feature ist nicht baubar,
   Aufwand null.
2. **Sie fällt nach einem Timeout auf Autostart zurück** und lädt mit ihrem
   eigenen Konfigurationswert.
3. **Sie fährt ihren App-Zeitplan**, falls einer gesetzt ist.

### Testablauf

Kostet eine Nacht und keinen Code.

1. Fahrzeug anstecken, Ladestand notieren.
2. In der myWallbox-App: Stromstärke auf 16 A, Box entsperrt. Falls die App
   einen Zeitplan kennt: 00:00–08:00 eintragen.
3. `docker compose stop pvueb` — die Box verliert den OCPP-Server.
4. Über Nacht laufen lassen.
5. Morgens auswerten: Ladestand des Fahrzeugs, Sitzungsenergie und
   `charging_time` in der Wallbox-App, sowie die Netzleistung in FusionSolar.
6. `docker compose start pvueb`.

**Auswertung:** Hat sie geladen? Ab wann? Mit welcher Leistung? Wenn sie erst
lud, nachdem die App-Sperre aufgehoben war, ist Verhalten 2 belegt. Lud sie
punktgenau ab 00:00, ist es Verhalten 3.

Ich kann Schritt 5 aus dem Mitschnitt und der Cloud auswerten, sobald der Pi
wieder läuft — der Cloud-Snapshot führt `added_energy`, `charging_time` und
`status_id` mit.

## Falls die Box offline lädt: der Hebel ist nicht der Zeitplan

Dann ist `locked` plus Stromstärke die einfachere Steuerung. Belegt ist bereits,
dass **`locked` unseren `RemoteStart` nicht blockiert** — die Ladung in der Nacht
zum 23.07. lief bei gesperrter Box an (Kommentar in `WALLBOX_STATUS`,
`charge_loop.py`). Die beiden Ebenen sind damit sauber getrennt: `locked`
steuert, was die Box **allein** tut, OCPP steuert, was **wir** tun.

| Zeit | Box-Einstellung | mit lebendem Pi | mit totem Pi |
|---|---|---|---|
| Tag | gesperrt, 6 A | PV-Überschussladen über `RemoteStart`, unverändert | nichts — kein Netzstrom |
| ab Sonnenuntergang | entsperrt, 16 A | Nachtfenster setzt ohnehin 16 A, kein Unterschied | Box lädt selbst, volle Leistung |

Zwei Schreibvorgänge am Tag statt einer Zeitplanverwaltung. Und die
Tagesabdeckung bleibt erhalten: ein toter Pi um 12 Uhr führt **nicht** dazu, dass
das Auto aus dem Netz lädt.

### Wann umgestellt wird

Nicht um feste 20:00 Uhr — im Juni liefert die Anlage bis nach 21:00. Sondern
**Sonnenuntergang plus Puffer**. Breiten- und Längengrad stehen schon in der
`.env` (`PVUEB_LAT`, `PVUEB_LON`), der Sonnenuntergang ist Trigonometrie ohne
zusätzliche Bibliothek. Zurückgestellt wird am Ende des Nachtfensters.

```
PVUEB_BOX_FALLBACK=1                  Feature an/aus, Default aus
PVUEB_BOX_FALLBACK_OFFSET_MIN=30      Minuten nach Sonnenuntergang
```

### Der Preis, ehrlich benannt

Schreibzugriff auf die Box gibt es nur über die **myWallbox-Cloud**; unser
Client (`read_wallbox_cloud.py`, `wallbox_cloud_task`) kann heute nur lesen. Die
Rückfallebene für „Pi tot" hinge damit am Internet.

Das ist tolerierbar, weil es zwei Schreibvorgänge am Tag sind und kein
Regelpfad — aber es verlangt zwingend, dass ein Fehlschlag gemeldet wird
(`feature_Benachrichtigung.md`, Regel „Wallbox-Rückfallebene nicht gesetzt",
Stufe `alarm`). Eine Rückfallebene, von der niemand weiß, dass sie nicht steht,
ist schlimmer als keine: sie erzeugt Vertrauen ohne Deckung.

Die Schreib-Endpunkte der Cloud sind nicht offiziell dokumentiert; als Referenz
dient dieselbe Sammlung wie für die Lesepfade
([SKB-CGN/wallbox](https://github.com/SKB-CGN/wallbox)). Sie können sich ohne
Vorwarnung ändern — auch das ein Grund, warum ein Fehlschlag laut sein muss.

## Was es nicht abdeckt

- Auto mit eigenem Ladetimer, das die Freigabe ignoriert
- Wallbox-Firmware, die selbst hängt
- Stromausfall im ganzen Haus
- Internet weg **und** Pi tot am selben Abend

Es deckt „Pi tot in der Nacht" ab. Das ist ein Fall, kein Schutzschirm — und er
sollte auch nicht als solcher verkauft werden.

## Reihenfolge

1. Messung (oben). Ohne sie kein Code.
2. Erst wenn Verhalten 2 oder 3 belegt ist: Schreibpfad in der Cloud-Anbindung,
   Sonnenuntergangsrechnung, Umstellung am Abend und am Fensterende.
3. Meldung bei Fehlschlag — zwingend gleichzeitig, nicht später.
