# Sicherheit

PVueb steuert eine Wallbox und kann die Hausbatterie aus dem Netz laden. Wer den
Dienst erreicht, kann beides. Das ist der Maßstab für alles hier.

## Wofür es gedacht ist

Ein Haushalt, ein LAN, drei Geräte. **Kein Port-Forwarding im Router, kein
Betrieb an einer öffentlichen Adresse.** Das ist keine Bequemlichkeit, sondern
die Grundannahme des Entwurfs: Die Anmeldung ist optional, es gibt keine
Sitzungsverwaltung, kein Rate-Limit am Web-UI und keine Härtung gegen einen
Angreifer, der schon im Netz steht.

Wer den Dienst von außen erreichbar machen will, stellt einen Reverse Proxy mit
TLS davor und schaltet die Anmeldung ein — und selbst dann bleibt die Wallbox
einen Klick entfernt.

## Was zu tun ist

**Ports ans LAN binden.** In der `docker-compose.yml` steht `"8080:8080"`, also
jede Schnittstelle des Hosts. In einem Netz, dem du nicht vollständig traust, die
LAN-Adresse voranstellen:

```yaml
ports:
  - "192.168.x.y:9000:9000"
  - "192.168.x.y:8080:8080"
```

Eine `ufw`-Regel genügt dafür **nicht** — Docker legt eigene iptables-Ketten an
und umgeht sie.

**Anmeldung einschalten**, sobald mehr als der eigene Haushalt im Netz ist:

```bash
PVUEB_WEB_USER=…      PVUEB_WEB_PASSWORD=…      # Web-UI
PVUEB_OCPP_USER=…     PVUEB_OCPP_PASSWORD=…     # OCPP-Endpunkt
```

Beim OCPP-Zugang die Reihenfolge beachten: **erst** Benutzer und Passwort in der
Wallbox-App eintragen, **dann** die `.env` setzen und neu starten — sonst kommt
die Box nicht mehr herein.

## Geheimnisse

`.env` enthält die Wechselrichter-IP, das myWallbox-Konto und die Passwörter für
Web-UI und OCPP. `LOCAL.md` enthält Adressen der eigenen Anlage. **Beide sind
gitignored und gehören nicht ins Repository.**

Der Dienst schreibt die `.env` beim Start zurück und legt dabei eine `.env.bak`
an — die enthält dieselben Geheimnisse. Kein Passwort wird geloggt, weder beim
Start noch im Fehlerfall.

## Meldeweg

`PVUEB_NOTIFY_URL` schickt Betriebsmeldungen nach außen. Deren Inhalt sagt Dinge
wie „Fahrzeug steckt, lädt seit 15 min nicht" — also auch, wann niemand zu Hause
ist. Zeigt die Adresse auf ein öffentliches ntfy-Thema, ist der Themenname das
einzige Geheimnis; er gehört gewürfelt, nicht ausgedacht.

## Eine Lücke melden

Privat, nicht als öffentliches Issue: über
[GitHub Security Advisories](https://github.com/MerowingerX/PVUeberschussLaden/security/advisories/new)
oder per E-Mail an den Repository-Inhaber.

Dies ist ein Freizeitprojekt für eine einzelne Anlage. Es gibt keine zugesagte
Reaktionszeit und keine Versionspflege — gepflegt wird der jeweils aktuelle Stand
auf `main`.
