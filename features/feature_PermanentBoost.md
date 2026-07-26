# Dauer-Boost aus voller Hausbatterie

**Status: umgesetzt (2026-07-25), Test am Gerät steht aus.**

Ausgangslage: An sonnigen Tagen steht die Hausbatterie mittags auf 100 % und
kann nichts mehr aufnehmen. Jede weitere Kilowattstunde geht für ein paar Cent
Einspeisevergütung ins Netz, während das Auto nur so viel bekommt, wie die PV
gerade liefert. Die Batterie soll besser genutzt werden.

## Umgesetzt

- Ab `PVUEB_PERMA_BOOST_ON_SOC` (Default 90 %) schiebt die Batterie dauerhaft
  `PVUEB_PERMA_BOOST_W` (Default 1000 W) zusätzlich ins Auto.
- Sie tut das, bis `PVUEB_PERMA_BOOST_OFF_SOC` (Default 50 %) erreicht ist;
  erst ab der oberen Schwelle springt sie wieder an.
- Der Beitrag kommt zu Start- und Wolkenloch-Boost hinzu und gilt auch vor dem
  Ladestart — eine Batterie an der oberen Schwelle kann ohnehin nichts mehr
  aufnehmen, die Ladung soll also früher anlaufen.
- Kein Tagesbudget: Die Spanne zwischen den beiden Schwellen *ist* das Budget.
  Was hier fließt, wird dem Budget des Wolkenloch-Boosts nicht angerechnet —
  sonst wäre das binnen Stunden leer und die Wolkenlöcher blieben ungefedert.
- `0` schaltet den Dauer-Boost ab.

Die beiden Schwellen sind eine Hysterese. Mit nur einer Schwelle ginge der
Boost an der Grenze im Regeltakt an und aus, weil sein eigener Verbrauch den
SOC sofort wieder darunter drückt.

## Autodetect sonniger Tage

Die Prognose wird dafür **nicht** abgefragt. Der SOC ist das bessere Signal:
Eine volle Batterie am Nachmittag ist der Beweis für den sonnigen Tag, den die
Prognose morgens nur behauptet. Fällt die Prognose daneben, bleibt der Boost
von allein aus. Nachgeladen wird die Batterie im normalen Ablauf wieder — Auto
und Box ziehen selten alles, und abends unter der Mindestladeleistung geht der
Überschuss ohnehin zurück in die Batterie.

## Entladegrenze (PVUEB_BATT_MAX_W)

Beim Testen zeigte sich: „oben drauf" geht nur, solange die Batterie es liefern
kann. `PVUEB_BOOST_W` steht auf 2500 W und entspricht damit der Entladegrenze
eines LUNA2000-Moduls — der Wolkenloch-Boost schöpft sie im Loch bereits aus.
Die zusätzlichen 1000 W kamen dann aus dem Netz (Test 12: 1,22 kWh statt 0,05).

Deshalb deckelt `PVUEB_BATT_MAX_W` (Default 2500) alle Boosts zusammen. Folge:

- **Sonne stabil**: Der Wolkenloch-Boost federt nichts ab, der Dauer-Boost
  wirkt voll.
- **Im Wolkenloch**: Der Wolkenloch-Boost hat Vorrang, der Dauer-Boost tritt
  zurück — die Batterie kann nun einmal nicht mehr.
- **Mehrere Batteriemodule**: `PVUEB_BATT_MAX_W` hochsetzen, dann wirken beide
  gleichzeitig.

## Messwerte aus der Simulation (poc/test_sim.py)

| Szenario | ohne Dauer-Boost | mit Dauer-Boost |
|---|---|---|
| Test 1 — sonniger Tag, Start bei SOC 50 % | 33,6 kWh, SOC 90 % | 36,3 kWh, SOC 56 % |
| Test 23 — sonniger Tag, Start bei SOC 95 % | — | 37,2 kWh, SOC 56 %, Netzbezug 0 |
| Test 12 — Dauerfeuer Wolkenlöcher, SOC 90 % | 37,0 kWh, Netz 0,05 kWh | 40,4 kWh, Netz 0,08 kWh |
| Test 24 — SOC 85 %, Batterie nimmt nichts auf | — | kein Dauer-Boost (Hysterese) |

Test 16 (Auto nimmt fest 6 A, Box meldet keine Leistung) zieht abends 0,11 kWh
aus dem Netz statt 0. Das ist die bekannte minpv-Mechanik: Unter der
Mindestleistung läuft die Ladung bis zum Timeout weiter, die Batterie steht am
Anschlag, den Rest füttert das Netz zu — wie in Test 8 auch ohne dieses
Feature.

## Zustand über Neustarts

Der Hysterese-Merker `perma_boost_aktiv` liegt in der Sitzungssicherung
([feature_NeustartOhneUnterbrechung.md](feature_NeustartOhneUnterbrechung.md)).
Ohne ihn stünde der Dauer-Boost nach einem Neustart bei 70 % SOC still, bis die
Batterie wieder 90 % erreicht.
