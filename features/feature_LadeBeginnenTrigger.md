# Fahrzeug eingestöpselt, PVÜberschuß oder PV Min ist aktiv

**Status: umgesetzt (2026-07-17), Test am Gerät steht aus.**

Intelligenter Starttrigger für den Modus PVUeb Min (minpv).

Ausgangslage beim Einstöpseln:
a) im Nachtfenster wird in jedem Fall geladen (unverändert)
b) minpv startete vorher bedingungslos und lud durch — jetzt Starttrigger + Wolkenloch-Timeout (siehe unten)
c) bei reinem PVUeb stoppt die Ladung nach `PVUEB_STOP_DELAY_S`, wenn der Überschuss unter 6 A fällt (unverändert)

## Umgesetzter Algorithmus (minpv)

Bezugsgröße: Min-Leistung = `min_amps × 3 Phasen × 230 V` (bei 6 A: 4140 W).
Echter PV-Überschuss = `Netz + Ladeleistung + Batterieleistung` — Hausbatterie-Ladung
zählt als verfügbar, Batterie-Entladung täuscht am Netzpunkt nur Überschuss vor
und wird abgezogen (sonst leert das Auto unbemerkt die LUNA2000).

1. **Start**: echter PV-Überschuss ≥ `START_FACTOR` × Min (Default 125 %),
   gehalten über `PVUEB_START_DELAY_S`.
2. **Laden**: Limit = max(min_amps, Überschuss-Ampere); fällt der Überschuss
   unter Min, füttert das Netz zu.
3. **Wolkenloch**: fällt der echte PV-Überschuss unter `PAUSE_FACTOR` × Min
   (Default 75 %), startet ein Timeout von `TIMEOUT_MIN` Minuten (Default 10).
4. **Erholung**: steigt er vor Ablauf über `RESUME_FACTOR` × Min (Default 90 %),
   wird der Timeout verworfen — Wolke überbrückt.
5. **Stopp**: läuft der Timeout ab, endet die Ladung; weiter bei 1.

Damit werden Wolken überbrückt; bei einbrechender Dunkelheit erholt sich der
Überschuss nicht mehr, der Timeout läuft ab und nachts übernimmt das
Nachtfenster.

## Konfiguration (.env, Bedingung 0 < pause < resume <= start)

```
PVUEB_MINPV_START_FACTOR=1.25
PVUEB_MINPV_PAUSE_FACTOR=0.75
PVUEB_MINPV_RESUME_FACTOR=0.90
PVUEB_MINPV_TIMEOUT_MIN=10
```

## Beantwortete Fragen

- **Einphasig laden (6 A ≈ 1380 W)?** Erledigt/verworfen: Denkfehler — der
  Wechselrichter verteilt seine Leistung dreiphasig, es sind immer alle drei
  Phasen ausgelastet. Einphasiges Laden kommt nicht vor.
- **Wolkenloch vs. Ladung beenden?** Gelöst über Hysterese (75 %/90 %) plus
  Timeout, siehe Algorithmus.

## Offen / Ideen

- Prognose-Abkürzung: bei Dämmerung (Rest-Erwartung laut forecast.solar unter
  Schwelle) sofort stoppen statt Timeout abwarten. Bisher nicht nötig — der
  Timeout erledigt das eine Wolkendauer später auch.
