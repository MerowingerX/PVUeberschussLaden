# PVueb — Android-App

Zeigt das Web-UI des Ladereglers als eigene App: eigenes Launcher-Icon, eigene
Task-Karte, Zurück-Taste schließt sie. Die Oberfläche selbst kommt vom Regler
(`poc/charge_loop.py`, `INDEX_HTML`) und ist hier bewusst **nicht** nachgebaut —
sie ändert sich mit dem Regler, und zwei Stellen für dieselben Knöpfe laufen
auseinander.

Dasselbe gilt für den zweiten Reiter: die Meldungen kommen als fertige Seite aus
`myhome-messenger` (eigenes Repository). Die App hält nur zwei WebViews, die
gemeinsame Logik dahinter steht einmal in `lib/web_seite.dart`.

## Einrichten am Gerät

Beim ersten Start fragt die App nach Host und Port des Reglers, z. B.
`192.168.1.50` und `8080` (Vorgabe des Reglers, änderbar über `--web-port`).
Benutzer und Passwort nur ausfüllen, wenn im Regler `PVUEB_WEB_USER` gesetzt
ist — die App beantwortet die
Basic-Auth-Abfrage dann selbst, statt eine leere Seite zu zeigen. Das Passwort
liegt im Keystore des Geräts, nicht in den SharedPreferences.

Darunter der Abschnitt **Meldestelle**: Port von `myhome-messenger` (Standard
8090), Benutzer und Passwort. Host und HTTPS-Schalter teilt sie sich mit dem
Regler — beide Dienste laufen auf demselben Rechner, und zwei Adressen zu
pflegen, die immer gleich sind, wäre nur eine Fehlerquelle mehr.

**Leerer Port heißt: keine Meldestelle.** Dann bleibt die Leiste unten weg und
die App sieht aus wie zuvor. Ist sie eingetragen, führt die Zurück-Taste aus den
Meldungen erst zum Regler und dann aus der App.

Damit das Telefon die Meldestelle erreicht, muss ihr Port aus dem Docker-Netz
heraus — und damit ist dort `MYHOME_WEB_USER` Pflicht. Steht im README des
Messengers.

HTTPS bleibt aus, solange der Regler direkt im LAN erreichbar ist. Klartext-HTTP
erlaubt `android/app/src/main/res/xml/network_security_config.xml`; wer den Port
nach außen öffnet, stellt einen Reverse Proxy davor und schaltet HTTPS ein.

## Bauen

```bash
make app-release        # aus dem Repo-Wurzelverzeichnis
```

Der `versionCode` kommt aus `git rev-list --count HEAD`, nicht aus
`pubspec.yaml` — ohne neuen Commit sieht kein Gerät ein Update.

## Icon

```bash
python3 tool/make_icon.py && dart run flutter_launcher_icons
```

`tool/make_icon.py` schreibt die PNGs ohne Pillow (siehe Kommentar dort).

## Veröffentlichen

Läuft über simonStore:

```bash
~/simonStore/bin/publish.sh ~/repositories/PVueb/app
```

`applicationId` ist `de.merowingerx.pvueb` und liegt fest — sie ändern hieße:
neue App, kein Update-Pfad, jedes Gerät muss die alte deinstallieren.
