# PVueb — Android-App

Zeigt das Web-UI des Ladereglers als eigene App: eigenes Launcher-Icon, eigene
Task-Karte, Zurück-Taste schließt sie. Die Oberfläche selbst kommt vom Regler
(`poc/charge_loop.py`, `INDEX_HTML`) und ist hier bewusst **nicht** nachgebaut —
sie ändert sich mit dem Regler, und zwei Stellen für dieselben Knöpfe laufen
auseinander.

## Einrichten am Gerät

Beim ersten Start fragt die App nach Host und Port des Reglers, z. B.
`192.168.1.50` und `8080` (Standard `PVUEB_WEB_PORT`). Benutzer und Passwort nur
ausfüllen, wenn im Regler `PVUEB_WEB_USER` gesetzt ist — die App beantwortet die
Basic-Auth-Abfrage dann selbst, statt eine leere Seite zu zeigen. Das Passwort
liegt im Keystore des Geräts, nicht in den SharedPreferences.

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
