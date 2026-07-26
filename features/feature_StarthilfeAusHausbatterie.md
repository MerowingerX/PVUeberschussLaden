# Starthilfe aus der Hausbatterie

**Status: umgesetzt (2026-07-25), Test am Gerät steht aus.**

Ausgangslage: Der Ladestart wartet auf echten PV-Überschuss von 110 % der
Min-Leistung (`PVUEB_MINPV_START_FACTOR`, bei 6 A also 4554 W). Fehlen daran ein
paar hundert Watt, bleibt die Ladung aus — obwohl eine volle Hausbatterie die
Lücke mühelos deckt.

## Umgesetzt

Ist der SOC über `PVUEB_BOOST_START_SOC` (Default 50 %) und Boost-Budget übrig,
füllt die Batterie bis zu `PVUEB_BOOST_START_W` (Default 500 W) an der Schwelle
fehlende Leistung auf:

- **vor dem Start**: bis `START_FACTOR × Min-Leistung` (minpv) bzw. bis 6 A (pv)
- **während der Ladung**: bis zur Min-Leistung — ohne das fiele der Start sofort
  in die Stopp-Schwelle zurück und es entstünde ein Start-Stopp-Kreisel
- gefüllt wird **nur die Lücke**: liegt die PV über der Schwelle, kostet die
  Starthilfe nichts

Verbraucht wird dasselbe Tagesbudget wie beim Wolkenloch-Boost
(`PVUEB_BOOST_W × PVUEB_BOOST_H`), gebucht über die tatsächliche
Batterieentladung. `PVUEB_BOOST_START_SOC` muss ≥ `PVUEB_BOOST_MIN_SOC` sein,
sonst startete die Ladung in einen Bereich, in dem der Boost sie nicht mehr
hält. `PVUEB_BOOST_START_W=0` schaltet die Starthilfe ab.

Warum kein pauschaler Zuschlag: Ein fester 500-W-Beitrag zieht auch dann aus der
Batterie, wenn die Sonne längst reicht — im Sim endete ein sonniger Tag damit
bei 60 % statt 100 % SOC. Nur-die-Lücke kostet am selben Tag 0,4 kWh Batterie
und bringt trotzdem 1,7 kWh mehr ins Auto (früherer Start am Morgen, späteres
Ende am Abend).

## Stopp-Schwellen

Bleiben unverändert. Die Starthilfe wirkt bei laufender Ladung bis zur
Min-Leistung weiter und hält die Ladung damit von selbst über der Pause-Schwelle
(minpv) bzw. über 6 A (pv), solange SOC und Budget reichen. Sind sie erschöpft,
fällt der verfügbare Überschuss um den Beitrag und die Ladung stoppt regulär —
ein Neustart scheitert dann an derselben Bedingung, es entsteht kein Pendeln.

## Tests (poc/test_sim.py)

- **Test 21**: 6,3 A Dauer-PV (0,3 A unter der Schwelle), SOC 70 % → Start;
  danach reicht die PV allein, Budgetverbrauch praktisch 0.
- **Test 22**: gleiche Lage mit `PVUEB_BOOST_START_W=0` → kein Start.
- **Test 10**: gleiche Lage mit SOC 45 % → kein Start (Starthilfe gesperrt).
