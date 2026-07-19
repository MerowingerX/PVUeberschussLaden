# Prüfbericht: Simulierte Regelung

Erzeugt am 19.07.2026 mit [`poc/test_sim.py`](../poc/test_sim.py) — der echte Regelcode aus [`poc/charge_loop.py`](../poc/charge_loop.py) läuft gegen ein Anlagenmodell, ohne Wechselrichter, Wallbox oder Fahrzeug.

```bash
cd poc && python test_sim.py            # diesen Bericht neu erzeugen
```

**Grenze dieser Tests:** Das Anlagenmodell stammt vom selben Autor wie der Regler. Wo die Vorstellung von der Anlage falsch ist, ist sie in beiden falsch — ein grüner Lauf ist dann kein Beweis, sondern ein Echo. Mitgeschnittene Tage aus dem echten Betrieb (`PVUEB_RECORD_DIR`) sind deshalb wertvoller als synthetische Kurven; [`poc/curve_from_recording.py`](../poc/curve_from_recording.py) macht sie nutzbar.

## Übersicht

| # | Szenario | Geladen | Netzbezug | Boost | SOC |
|---|---|--:|--:|--:|---|
| 1 | sonniger Tag, Glockenkurve bis 10 A, minpv 6 A, 8 h | 31.9 kWh | 0.00 kWh | 1.01 kWh | 50 → 99 % |
| 2 | bewölkt mit Sonnenstunden, 8 h, minpv 6 A | 36.8 kWh | 0.01 kWh | 3.11 kWh | 50 → 71 % |
| 3 | Anlauf auf 8 A, dann dauerhaft 3,5 A, minpv 6 A | 4.3 kWh | 0.00 kWh | 0.61 kWh | 50 → 100 % |
| 4 | wie Test 3, aber Modus pv (kein Mindeststrom) | 3.8 kWh | 0.00 kWh | 0.27 kWh | 50 → 100 % |
| 5 | nur kurze Spitzen (2 min über Schwelle, 8 min darunter) | 0.0 kWh | 0.00 kWh | 0.00 kWh | 50 → 100 % |
| 6 | Anlauf auf 8 A, dann 3 h bei 4 A, minpv 6 A | 4.2 kWh | 0.00 kWh | 0.51 kWh | 50 → 100 % |
| 7 | wie Test 3, aber SOC 25 % (unter boost_min_soc 30 %) | 3.7 kWh | 0.00 kWh | 0.00 kWh | 25 → 19 % |
| 8 | voller Anlauf, dann harter Abriss auf 0 A | 9.8 kWh | 0.28 kWh | 0.42 kWh | 50 → 43 % |
| 9 | Nachtfenster ohne PV | 22.1 kWh | 19.81 kWh | 0.00 kWh | 50 → 5 % |
| 10 | Dauerhaft 6,3 A bei minpv 6 A (Startschwelle 1,10 × 6 A = 6,6 A) | 0.0 kWh | 0.00 kWh | 0.00 kWh | 50 → 100 % |
| 11 | wie Test 10, aber Modus pv | 9.6 kWh | 0.00 kWh | 0.00 kWh | 50 → 81 % |
| 12 | Dauerfeuer kurzer Wolkenlöcher (6 min alle 20 min), 8 h | 36.9 kWh | 0.05 kWh | 5.00 kWh | 90 → 99 % |
| 13 | wie Test 1, aber Wallbox meldet keine Ladeleistung | 31.9 kWh | 0.00 kWh | 1.01 kWh | 50 → 99 % |
| 14 | wie Test 1, aber Ladeleistung eingefroren (letzte Meldung 5 min alt) | 31.9 kWh | 0.00 kWh | 1.01 kWh | 50 → 99 % |
| 15 | Wallbox meldet Ladung, Auto nimmt nichts an | 0.0 kWh | 0.00 kWh | 0.00 kWh | 50 → 100 % |
| 16 | Auto nimmt nur 6 A, Wallbox meldet keine Ladeleistung | 23.1 kWh | 0.00 kWh | 0.71 kWh | 50 → 100 % |
| 17 | Box lehnt Start ab (hängt in Finishing) | 31.9 kWh | 0.00 kWh | 1.01 kWh | 50 → 99 % |
| 18 | Fahrzeug voll (SuspendedEV), Box nimmt keinen Start an | 0.0 kWh | 0.00 kWh | 0.00 kWh | 50 → 100 % |

## Verläufe

### 1. sonniger Tag, Glockenkurve bis 10 A, minpv 6 A, 8 h

> Erwartet: ein Start am Vormittag, Limit folgt der Kurve, ein Stopp am Abend.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.2    0.0 A   ·      +836 W       +0 W   52.1 %
  00:30    2.3    0.0 A   ·     +1617 W       +0 W   58.3 %
  00:45    3.4    0.0 A   ·     +2345 W       +0 W   68.2 %
  01:00    4.4    0.0 A   ·     +2500 W     +519 W   80.6 %
  01:15    5.3    0.0 A   ·     +2500 W    +1139 W   93.1 %
  01:30    6.1    0.0 A   ·        +0 W    +4205 W  100.0 %
  01:45    6.8    6.6 A  ⚡        +0 W     +163 W  100.0 %
  02:00    7.5    6.9 A  ⚡        +0 W     +414 W  100.0 %
  02:15    8.1    7.7 A  ⚡        +0 W     +266 W  100.0 %
  02:30    8.6    8.4 A  ⚡        +0 W     +134 W  100.0 %
  02:45    9.0    8.8 A  ⚡        +0 W     +154 W  100.0 %
  03:00    9.4    9.2 A  ⚡        +0 W     +121 W  100.0 %
  03:15    9.6    9.5 A  ⚡        +0 W     +102 W  100.0 %
  03:30    9.8    9.5 A  ⚡        +0 W     +237 W  100.0 %
  03:45   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:00   10.0    9.8 A  ⚡        +0 W     +138 W  100.0 %
  04:15   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:30    9.8    9.8 A  ⚡        +0 W      +30 W  100.0 %
  04:45    9.6    9.8 A  ⚡      -105 W       +0 W   99.8 %
  05:00    9.4    9.5 A  ⚡       -86 W       +0 W   99.3 %
  05:15    9.0    9.2 A  ⚡      -122 W       +0 W   98.8 %
  05:30    8.6    8.8 A  ⚡      -142 W       +0 W   97.9 %
  05:45    8.1    8.2 A  ⚡       -79 W       +0 W   97.1 %
  06:00    7.5    7.8 A  ⚡      -207 W       +0 W   96.2 %
  06:15    6.8    7.1 A  ⚡      -182 W       +0 W   95.2 %
  06:30    6.1    6.3 A  ⚡      -142 W       +0 W   93.9 %
  06:45    5.3    6.3 A  ⚡      -708 W       +0 W   91.8 %
  07:00    4.4    6.3 A  ⚡     -1328 W       +0 W   86.7 %
  07:15    3.4    6.3 A   ·     +2345 W       +0 W   81.3 %
  07:30    2.3    6.3 A   ·     +1617 W       +0 W   91.2 %
  07:45    1.2    6.3 A   ·      +836 W       +0 W   97.4 %

  Ereignisse:
    01:43 Limit 6.6 A
    01:43 START
    01:51 Limit 6.9 A
    02:00 Limit 7.3 A
    02:10 Limit 7.7 A
    02:20 Limit 8.1 A
    02:29 Limit 8.4 A
    02:41 Limit 8.8 A
    02:57 Limit 9.2 A
    03:11 Limit 9.5 A
    03:31 Limit 9.8 A
    04:52 Limit 9.5 A
    05:08 Limit 9.2 A
    05:24 Limit 8.8 A
    05:34 Limit 8.5 A
    05:43 Limit 8.2 A
    05:55 Limit 7.8 A
    06:05 Limit 7.4 A
    06:12 Limit 7.1 A
    06:20 Limit 6.7 A
    06:29 Limit 6.3 A
    07:13 STOPP

  Geladen: 31.9 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 1.01 kWh | SOC 50 % -> 99 %
```

### 2. bewölkt mit Sonnenstunden, 8 h, minpv 6 A

> Erwartet: Wolkenlöcher (8 min) werden von Boost + Timeout überbrückt, keine Stopps.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.7    0.0 A   ·     +1150 W       +0 W   52.9 %
  00:30    3.3    0.0 A   ·     +2300 W       +0 W   61.5 %
  00:45    5.0    0.0 A   ·     +2500 W     +950 W   73.9 %
  01:00    6.7    0.0 A   ·     +2500 W    +2100 W   86.4 %
  01:15    8.3    7.6 A  ⚡      +506 W       +0 W   90.6 %
  01:30   10.0    9.3 A  ⚡      +483 W       +0 W   93.1 %
  01:45   10.0   10.0 A  ⚡        +0 W       +0 W   94.1 %
  02:00   10.0   10.0 A  ⚡        +0 W       +0 W   94.1 %
  02:15   10.0   10.0 A  ⚡        +0 W       +0 W   94.1 %
  02:30    3.0    6.6 A  ⚡     -2500 W    -2330 W   94.0 %
  02:45   10.0    7.7 A  ⚡     +1587 W       +0 W   93.3 %
  03:00    7.0    7.1 A  ⚡       -69 W       +0 W   90.5 %
  03:15    7.0    7.1 A  ⚡       -69 W       +0 W   90.1 %
  03:30    3.0    6.6 A  ⚡     -2500 W     -329 W   89.7 %
  03:45   10.0    7.7 A  ⚡     +1587 W       +0 W   89.3 %
  04:00    7.0    7.1 A  ⚡       -69 W       +0 W   86.5 %
  04:15    7.0    7.1 A  ⚡       -69 W       +0 W   86.2 %
  04:30    3.0    6.6 A  ⚡     -2500 W     -329 W   85.8 %
  04:45   10.0    7.7 A  ⚡     +1587 W       +0 W   85.4 %
  05:00    7.0    7.1 A  ⚡       -69 W       +0 W   82.6 %
  05:15    7.0    7.1 A  ⚡       -69 W       +0 W   82.2 %
  05:30    3.0    6.6 A  ⚡     -2500 W     -329 W   81.8 %
  05:45   10.0    7.7 A  ⚡     +1587 W       +0 W   81.4 %
  06:00    7.0    7.1 A  ⚡       -69 W       +0 W   78.6 %
  06:15    7.0    7.1 A  ⚡       -69 W       +0 W   78.3 %
  06:30    3.0    6.6 A  ⚡     -2500 W     -329 W   77.9 %
  06:45   10.0    7.7 A  ⚡     +1587 W       +0 W   77.5 %
  07:00    7.0    7.1 A  ⚡       -69 W       +0 W   74.7 %
  07:15    7.0    7.1 A  ⚡       -69 W       +0 W   74.3 %
  07:30    3.0    6.6 A  ⚡     -2500 W     -329 W   73.9 %
  07:45   10.0    7.7 A  ⚡     +1587 W       +0 W   73.5 %

  Ereignisse:
    01:02 Limit 6.8 A
    01:02 START
    01:02 Limit 6.4 A
    01:06 Limit 6.8 A
    01:09 Limit 7.2 A
    01:13 Limit 7.6 A
    01:16 Limit 7.9 A
    01:19 Limit 8.3 A
    01:23 Limit 8.7 A
    01:26 Limit 9.0 A
    01:28 Limit 9.3 A
    01:32 Limit 9.7 A
    01:40 Limit 10.0 A
    02:30 Limit 6.6 A
    02:35 Limit 6.2 A
    02:43 Limit 6.6 A
    02:43 Limit 6.9 A
    02:44 Limit 7.3 A
    02:44 Limit 7.7 A
    02:45 Limit 8.1 A
    02:45 Limit 8.4 A
    02:46 Limit 8.8 A
    02:46 Limit 9.2 A
    02:47 Limit 9.5 A
    02:47 Limit 9.8 A
    02:49 Limit 9.5 A
    02:50 Limit 9.2 A
    02:51 Limit 8.8 A
    02:52 Limit 8.5 A
    02:53 Limit 8.2 A
    02:55 Limit 7.8 A
    02:56 Limit 7.4 A
    02:57 Limit 7.1 A
    03:30 Limit 6.6 A
    03:31 Limit 6.2 A
    03:43 Limit 6.6 A
    03:43 Limit 6.9 A
    03:44 Limit 7.3 A
    03:44 Limit 7.7 A
    03:45 Limit 8.1 A
    03:45 Limit 8.4 A
    03:46 Limit 8.8 A
    03:46 Limit 9.2 A
    03:47 Limit 9.5 A
    03:47 Limit 9.8 A
    03:49 Limit 9.5 A
    03:50 Limit 9.2 A
    03:51 Limit 8.8 A
    03:52 Limit 8.5 A
    03:53 Limit 8.2 A
    03:55 Limit 7.8 A
    03:56 Limit 7.4 A
    03:57 Limit 7.1 A
    04:30 Limit 6.6 A
    04:31 Limit 6.2 A
    04:43 Limit 6.6 A
    04:43 Limit 6.9 A
    04:44 Limit 7.3 A
    04:44 Limit 7.7 A
    04:45 Limit 8.1 A
    04:45 Limit 8.4 A
    04:46 Limit 8.8 A
    04:46 Limit 9.2 A
    04:47 Limit 9.5 A
    04:47 Limit 9.8 A
    04:49 Limit 9.5 A
    04:50 Limit 9.2 A
    04:51 Limit 8.8 A
    04:52 Limit 8.5 A
    04:53 Limit 8.2 A
    04:55 Limit 7.8 A
    04:56 Limit 7.4 A
    04:57 Limit 7.1 A
    05:30 Limit 6.6 A
    05:31 Limit 6.2 A
    05:43 Limit 6.6 A
    05:43 Limit 6.9 A
    05:44 Limit 7.3 A
    05:44 Limit 7.7 A
    05:45 Limit 8.1 A
    05:45 Limit 8.4 A
    05:46 Limit 8.8 A
    05:46 Limit 9.2 A
    05:47 Limit 9.5 A
    05:47 Limit 9.8 A
    05:49 Limit 9.5 A
    05:50 Limit 9.2 A
    05:51 Limit 8.8 A
    05:52 Limit 8.5 A
    05:53 Limit 8.2 A
    05:55 Limit 7.8 A
    05:56 Limit 7.4 A
    05:57 Limit 7.1 A
    06:30 Limit 6.6 A
    06:31 Limit 6.2 A
    06:43 Limit 6.6 A
    06:43 Limit 6.9 A
    06:44 Limit 7.3 A
    06:44 Limit 7.7 A
    06:45 Limit 8.1 A
    06:45 Limit 8.4 A
    06:46 Limit 8.8 A
    06:46 Limit 9.2 A
    06:47 Limit 9.5 A
    06:47 Limit 9.8 A
    06:49 Limit 9.5 A
    06:50 Limit 9.2 A
    06:51 Limit 8.8 A
    06:52 Limit 8.5 A
    06:53 Limit 8.2 A
    06:55 Limit 7.8 A
    06:56 Limit 7.4 A
    06:57 Limit 7.1 A
    07:30 Limit 6.6 A
    07:31 Limit 6.2 A
    07:43 Limit 6.6 A
    07:43 Limit 6.9 A
    07:44 Limit 7.3 A
    07:44 Limit 7.7 A
    07:45 Limit 8.1 A
    07:45 Limit 8.4 A
    07:46 Limit 8.8 A
    07:46 Limit 9.2 A
    07:47 Limit 9.5 A
    07:47 Limit 9.8 A
    07:49 Limit 9.5 A
    07:50 Limit 9.2 A
    07:51 Limit 8.8 A
    07:52 Limit 8.5 A
    07:53 Limit 8.2 A
    07:55 Limit 7.8 A
    07:56 Limit 7.4 A
    07:57 Limit 7.1 A

  Geladen: 36.8 kWh | Netzbezug: 0.01 kWh | Boost verbraucht: 3.11 kWh | SOC 50 % -> 71 %
```

### 3. Anlauf auf 8 A, dann dauerhaft 3,5 A, minpv 6 A

> Erwartet: Start, dann Boost hält 6 A, bis der minpv-Timeout (10 min) die Ladung stoppt.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    2.7    0.0 A   ·     +1840 W       +0 W   54.6 %
  00:30    5.3    0.0 A   ·     +2500 W    +1180 W   66.5 %
  00:45    8.0    7.1 A  ⚡      +897 W       +0 W   76.2 %
  01:00    8.0    7.8 A  ⚡      +138 W       +0 W   77.5 %
  01:15    3.5    7.1 A  ⚡     -2500 W     -467 W   78.1 %
  01:30    3.5    6.3 A  ⚡     -1932 W       +0 W   67.9 %
  01:45    3.5    6.3 A   ·     +2415 W       +0 W   75.8 %
  02:00    3.5    6.3 A   ·     +2415 W       +0 W   87.9 %
  02:15    3.5    6.3 A   ·     +2415 W       +0 W  100.0 %
  02:30    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  02:45    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:00    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:15    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:30    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:45    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %

  Ereignisse:
    00:40 Limit 6.9 A
    00:40 START
    00:40 Limit 6.3 A
    00:42 Limit 6.7 A
    00:45 Limit 7.1 A
    00:46 Limit 7.4 A
    00:50 Limit 7.8 A
    01:15 Limit 7.1 A
    01:17 Limit 6.7 A
    01:18 Limit 6.3 A
    01:32 STOPP

  Geladen: 4.3 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.61 kWh | SOC 50 % -> 100 %
```

### 4. wie Test 3, aber Modus pv (kein Mindeststrom)

> Erwartet: Start, Limit fällt mit dem Mittel, Stopp über stop_delay statt Timeout.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    2.7    0.0 A   ·     +1840 W       +0 W   54.6 %
  00:30    5.3    0.0 A   ·     +2500 W    +1180 W   66.5 %
  00:45    8.0    7.1 A  ⚡      +897 W       +0 W   73.8 %
  01:00    8.0    7.8 A  ⚡      +138 W       +0 W   75.1 %
  01:15    3.5    7.1 A  ⚡     -2500 W     -467 W   75.7 %
  01:30    3.5    6.3 A   ·     +2415 W       +0 W   76.5 %
  01:45    3.5    6.3 A   ·     +2415 W       +0 W   88.6 %
  02:00    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  02:15    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  02:30    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  02:45    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:00    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:15    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:30    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %
  03:45    3.5    6.3 A   ·        +0 W    +2415 W  100.0 %

  Ereignisse:
    00:36 Limit 6.3 A
    00:36 START
    00:42 Limit 6.7 A
    00:45 Limit 7.1 A
    00:46 Limit 7.4 A
    00:50 Limit 7.8 A
    01:15 Limit 7.1 A
    01:17 Limit 6.7 A
    01:18 Limit 6.3 A
    01:22 STOPP

  Geladen: 3.8 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.27 kWh | SOC 50 % -> 100 %
```

### 5. nur kurze Spitzen (2 min über Schwelle, 8 min darunter)

> Erwartet: gar kein Start — start_delay 120 s wird nie durchgehalten.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    8.0    0.0 A   ·     +2500 W    +3020 W   50.1 %
  00:15    2.0    0.0 A   ·     +1380 W       +0 W   58.4 %
  00:30    8.0    0.0 A   ·     +2500 W    +3020 W   66.1 %
  00:45    2.0    0.0 A   ·     +1380 W       +0 W   74.5 %
  01:00    8.0    0.0 A   ·     +2500 W    +3020 W   82.1 %
  01:15    2.0    0.0 A   ·     +1380 W       +0 W   90.5 %
  01:30    8.0    0.0 A   ·     +2500 W    +3020 W   98.2 %
  01:45    2.0    0.0 A   ·        +0 W    +1380 W  100.0 %

  Ereignisse:

  Geladen: 0.0 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.00 kWh | SOC 50 % -> 100 %
```

### 6. Anlauf auf 8 A, dann 3 h bei 4 A, minpv 6 A

> Erwartet: Boost federt den Einbruch ab, das 10-min-Mittel zieht das Ziel aber unter die Pause-Schwelle -> Timeout -> Stopp, lange bevor das Budget leer ist.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    4.0    0.0 A   ·     +2500 W     +260 W   56.9 %
  00:30    8.0    6.4 A  ⚡     +1104 W       +0 W   68.2 %
  00:45    8.0    7.9 A  ⚡       +69 W       +0 W   69.7 %
  01:00    4.0    7.6 A  ⚡     -2500 W     -191 W   70.0 %
  01:15    4.0    6.1 A  ⚡     -1449 W       +0 W   61.7 %
  01:30    4.0    6.1 A   ·     +2500 W     +260 W   69.2 %
  01:45    4.0    6.1 A   ·     +2500 W     +260 W   81.7 %
  02:00    4.0    6.1 A   ·     +2500 W     +260 W   94.2 %
  02:15    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  02:30    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  02:45    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  03:00    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  03:15    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  03:30    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  03:45    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  04:00    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  04:15    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  04:30    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %
  04:45    4.0    6.1 A   ·        +0 W    +2760 W  100.0 %

  Ereignisse:
    00:27 Limit 7.1 A
    00:27 START
    00:28 Limit 6.1 A
    00:29 Limit 6.4 A
    00:30 Limit 6.8 A
    00:32 Limit 7.2 A
    00:34 Limit 7.6 A
    00:37 Limit 7.9 A
    01:00 Limit 7.6 A
    01:01 Limit 7.2 A
    01:02 Limit 6.8 A
    01:03 Limit 6.4 A
    01:04 Limit 6.1 A
    01:18 STOPP

  Geladen: 4.2 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.51 kWh | SOC 50 % -> 100 %
```

### 7. wie Test 3, aber SOC 25 % (unter boost_min_soc 30 %)

> Erwartet: kein Boost, Ladung stoppt früher, Batteriereserve bleibt unangetastet.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   25.0 %
  00:15    2.7    0.0 A   ·        +0 W    +1840 W   25.0 %
  00:30    5.3    0.0 A   ·        +0 W    +3680 W   25.0 %
  00:45    8.0    7.1 A  ⚡        +0 W     +897 W   25.0 %
  01:00    8.0    7.8 A  ⚡        +0 W     +138 W   25.0 %
  01:15    3.5    6.0 A  ⚡     -2500 W     -467 W   24.9 %
  01:30    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  01:45    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  02:00    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  02:15    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  02:30    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  02:45    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  03:00    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  03:15    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  03:30    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %
  03:45    3.5    6.0 A   ·        +0 W    +2415 W   19.2 %

  Ereignisse:
    00:40 Limit 6.9 A
    00:40 START
    00:40 Limit 6.3 A
    00:42 Limit 6.7 A
    00:45 Limit 7.1 A
    00:46 Limit 7.4 A
    00:50 Limit 7.8 A
    01:15 Limit 6.0 A
    01:25 STOPP

  Geladen: 3.7 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.00 kWh | SOC 25 % -> 19 %
```

### 8. voller Anlauf, dann harter Abriss auf 0 A

> Erwartet: Boost + Timeout federn ab, Stopp danach, Netzbezug begrenzt.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    9.0    0.0 A   ·     +2500 W    +3710 W   50.1 %
  00:15    9.0    9.0 A  ⚡        +0 W       +0 W   51.8 %
  00:30    9.0    9.0 A  ⚡        +0 W       +0 W   51.8 %
  00:45    9.0    9.0 A  ⚡        +0 W       +0 W   51.8 %
  01:00    9.0    9.0 A  ⚡        +0 W       +0 W   51.8 %
  01:15    9.0    9.0 A  ⚡        +0 W       +0 W   51.8 %
  01:30    0.0    6.0 A  ⚡     -2500 W    -3710 W   51.7 %
  01:45    0.0    6.0 A   ·        +0 W       +0 W   43.4 %
  02:00    0.0    6.0 A   ·        +0 W       +0 W   43.4 %
  02:15    0.0    6.0 A   ·        +0 W       +0 W   43.4 %
  02:30    0.0    6.0 A   ·        +0 W       +0 W   43.4 %
  02:45    0.0    6.0 A   ·        +0 W       +0 W   43.4 %

  Ereignisse:
    00:02 Limit 9.0 A
    00:02 START
    01:30 Limit 6.0 A
    01:40 STOPP

  Geladen: 9.8 kWh | Netzbezug: 0.28 kWh | Boost verbraucht: 0.42 kWh | SOC 50 % -> 43 %
```

### 9. Nachtfenster ohne PV

> Erwartet: sofort 16 A aus dem Netz, unabhängig vom Modus.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0   16.0 A  ⚡        +0 W       +0 W   50.0 %
  00:15    0.0   16.0 A  ⚡     -2500 W    -8540 W   37.5 %
  00:30    0.0   16.0 A  ⚡     -2500 W    -8540 W   25.0 %
  00:45    0.0   16.0 A  ⚡     -2500 W    -8540 W   12.5 %
  01:00    0.0   16.0 A  ⚡        +0 W   -11040 W    4.9 %
  01:15    0.0   16.0 A  ⚡        +0 W   -11040 W    4.9 %
  01:30    0.0   16.0 A  ⚡        +0 W   -11040 W    4.9 %
  01:45    0.0   16.0 A  ⚡        +0 W   -11040 W    4.9 %

  Ereignisse:
    00:00 Limit 16.0 A
    00:00 START

  Geladen: 22.1 kWh | Netzbezug: 19.81 kWh | Boost verbraucht: 0.00 kWh | SOC 50 % -> 5 %
```

### 10. Dauerhaft 6,3 A bei minpv 6 A (Startschwelle 1,10 × 6 A = 6,6 A)

> Erwartet: kein Start — 6,3 A reichen für den minpv-Trigger nicht.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    2.1    0.0 A   ·     +1449 W       +0 W   53.6 %
  00:30    4.2    0.0 A   ·     +2500 W     +398 W   64.3 %
  00:45    6.3    0.0 A   ·     +2500 W    +1847 W   76.8 %
  01:00    6.3    0.0 A   ·     +2500 W    +1847 W   89.3 %
  01:15    6.3    0.0 A   ·        +0 W    +4347 W  100.0 %
  01:30    6.3    0.0 A   ·        +0 W    +4347 W  100.0 %
  01:45    6.3    0.0 A   ·        +0 W    +4347 W  100.0 %
  02:00    6.3    0.0 A   ·        +0 W    +4347 W  100.0 %
  02:15    6.3    0.0 A   ·        +0 W    +4347 W  100.0 %
  02:30    6.3    0.0 A   ·        +0 W    +4347 W  100.0 %
  02:45    6.3    0.0 A   ·        +0 W    +4347 W  100.0 %

  Ereignisse:

  Geladen: 0.0 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.00 kWh | SOC 50 % -> 100 %
```

### 11. wie Test 10, aber Modus pv

> Erwartet: Start bei 6 A — ohne minpv-Trigger genügt das Mittel über 6 A.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    2.1    0.0 A   ·     +1449 W       +0 W   53.6 %
  00:30    4.2    0.0 A   ·     +2500 W     +398 W   64.3 %
  00:45    6.3    0.0 A   ·     +2500 W    +1847 W   76.8 %
  01:00    6.3    6.2 A  ⚡       +69 W       +0 W   77.8 %
  01:15    6.3    6.2 A  ⚡       +69 W       +0 W   78.2 %
  01:30    6.3    6.2 A  ⚡       +69 W       +0 W   78.5 %
  01:45    6.3    6.2 A  ⚡       +69 W       +0 W   78.9 %
  02:00    6.3    6.2 A  ⚡       +69 W       +0 W   79.2 %
  02:15    6.3    6.2 A  ⚡       +69 W       +0 W   79.6 %
  02:30    6.3    6.2 A  ⚡       +69 W       +0 W   79.9 %
  02:45    6.3    6.2 A  ⚡       +69 W       +0 W   80.3 %

  Ereignisse:
    00:45 Limit 6.2 A
    00:45 START

  Geladen: 9.6 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.00 kWh | SOC 50 % -> 81 %
```

### 12. Dauerfeuer kurzer Wolkenlöcher (6 min alle 20 min), 8 h

> Erwartet: Boost überbrückt jedes Loch, bis das Tagesbudget (5 kWh) leer ist — danach greift der Timeout.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   90.0 %
  00:15    4.5    0.0 A   ·     +2500 W     +605 W   97.5 %
  00:30    3.0    6.6 A  ⚡     -2500 W     -398 W   99.9 %
  00:45    9.0    8.4 A  ⚡        +0 W     +621 W  100.0 %
  01:00    9.0    6.2 A  ⚡     +1932 W       +0 W   97.8 %
  01:15    3.0    6.2 A  ⚡     -2208 W       +0 W   95.8 %
  01:30    3.0    6.6 A  ⚡     -2500 W    -1502 W   99.9 %
  01:45    9.0    8.4 A  ⚡        +0 W     +621 W  100.0 %
  02:00    9.0    6.2 A  ⚡     +1932 W       +0 W   97.8 %
  02:15    3.0    6.2 A  ⚡     -2208 W       +0 W   95.8 %
  02:30    3.0    6.6 A  ⚡     -2500 W    -1502 W   99.9 %
  02:45    9.0    8.4 A  ⚡        +0 W     +621 W  100.0 %
  03:00    9.0    6.2 A  ⚡     +1932 W       +0 W   97.8 %
  03:15    3.0    6.2 A  ⚡     -2208 W       +0 W   95.8 %
  03:30    3.0    6.6 A  ⚡     -2500 W    -1502 W   99.9 %
  03:45    9.0    8.4 A  ⚡        +0 W     +621 W  100.0 %
  04:00    9.0    6.2 A  ⚡     +1932 W       +0 W   97.8 %
  04:15    3.0    6.2 A  ⚡     -2208 W       +0 W   95.8 %
  04:30    3.0    6.6 A  ⚡     -2500 W    -1502 W   99.9 %
  04:45    9.0    8.4 A  ⚡        +0 W     +621 W  100.0 %
  05:00    9.0    6.2 A  ⚡     +1932 W       +0 W   97.8 %
  05:15    3.0    6.2 A  ⚡     -2208 W       +0 W   95.8 %
  05:30    3.0    6.6 A  ⚡     -2500 W    -1502 W   99.9 %
  05:45    9.0    8.4 A  ⚡        +0 W     +621 W  100.0 %
  06:00    9.0    6.2 A  ⚡     +1932 W       +0 W   97.8 %
  06:15    3.0    6.2 A  ⚡     -2208 W       +0 W   95.8 %
  06:30    3.0    6.6 A  ⚡     -2500 W    -1502 W   99.9 %
  06:45    9.0    8.4 A  ⚡        +0 W     +621 W  100.0 %
  07:00    9.0    6.2 A  ⚡     +1932 W       +0 W   97.8 %
  07:15    3.0    6.0 A  ⚡     -2070 W       +0 W   96.1 %
  07:30    3.0    6.0 A  ⚡     -2500 W    -1640 W   99.9 %
  07:45    9.0    8.3 A  ⚡        +0 W     +483 W  100.0 %

  Ereignisse:
    00:25 Limit 7.2 A
    00:25 START
    00:25 Limit 6.1 A
    00:26 Limit 6.4 A
    00:27 Limit 6.8 A
    00:29 Limit 7.2 A
    00:30 Limit 6.6 A
    00:33 Limit 6.2 A
    00:42 Limit 6.6 A
    00:42 Limit 6.9 A
    00:43 Limit 7.3 A
    00:43 Limit 7.7 A
    00:44 Limit 8.1 A
    00:45 Limit 8.4 A
    00:45 Limit 8.8 A
    00:50 Limit 6.6 A
    00:54 Limit 6.2 A
    01:02 Limit 6.6 A
    01:02 Limit 6.9 A
    01:03 Limit 7.3 A
    01:03 Limit 7.7 A
    01:04 Limit 8.1 A
    01:05 Limit 8.4 A
    01:05 Limit 8.8 A
    01:10 Limit 6.6 A
    01:14 Limit 6.2 A
    01:22 Limit 6.6 A
    01:22 Limit 6.9 A
    01:23 Limit 7.3 A
    01:23 Limit 7.7 A
    01:24 Limit 8.1 A
    01:25 Limit 8.4 A
    01:25 Limit 8.8 A
    01:30 Limit 6.6 A
    01:34 Limit 6.2 A
    01:42 Limit 6.6 A
    01:42 Limit 6.9 A
    01:43 Limit 7.3 A
    01:43 Limit 7.7 A
    01:44 Limit 8.1 A
    01:45 Limit 8.4 A
    01:45 Limit 8.8 A
    01:50 Limit 6.6 A
    01:54 Limit 6.2 A
    02:02 Limit 6.6 A
    02:02 Limit 6.9 A
    02:03 Limit 7.3 A
    02:03 Limit 7.7 A
    02:04 Limit 8.1 A
    02:05 Limit 8.4 A
    02:05 Limit 8.8 A
    02:10 Limit 6.6 A
    02:14 Limit 6.2 A
    02:22 Limit 6.6 A
    02:22 Limit 6.9 A
    02:23 Limit 7.3 A
    02:23 Limit 7.7 A
    02:24 Limit 8.1 A
    02:25 Limit 8.4 A
    02:25 Limit 8.8 A
    02:30 Limit 6.6 A
    02:34 Limit 6.2 A
    02:42 Limit 6.6 A
    02:42 Limit 6.9 A
    02:43 Limit 7.3 A
    02:43 Limit 7.7 A
    02:44 Limit 8.1 A
    02:45 Limit 8.4 A
    02:45 Limit 8.8 A
    02:50 Limit 6.6 A
    02:54 Limit 6.2 A
    03:02 Limit 6.6 A
    03:02 Limit 6.9 A
    03:03 Limit 7.3 A
    03:03 Limit 7.7 A
    03:04 Limit 8.1 A
    03:05 Limit 8.4 A
    03:05 Limit 8.8 A
    03:10 Limit 6.6 A
    03:14 Limit 6.2 A
    03:22 Limit 6.6 A
    03:22 Limit 6.9 A
    03:23 Limit 7.3 A
    03:23 Limit 7.7 A
    03:24 Limit 8.1 A
    03:25 Limit 8.4 A
    03:25 Limit 8.8 A
    03:30 Limit 6.6 A
    03:34 Limit 6.2 A
    03:42 Limit 6.6 A
    03:42 Limit 6.9 A
    03:43 Limit 7.3 A
    03:43 Limit 7.7 A
    03:44 Limit 8.1 A
    03:45 Limit 8.4 A
    03:45 Limit 8.8 A
    03:50 Limit 6.6 A
    03:54 Limit 6.2 A
    04:02 Limit 6.6 A
    04:02 Limit 6.9 A
    04:03 Limit 7.3 A
    04:03 Limit 7.7 A
    04:04 Limit 8.1 A
    04:05 Limit 8.4 A
    04:05 Limit 8.8 A
    04:10 Limit 6.6 A
    04:14 Limit 6.2 A
    04:22 Limit 6.6 A
    04:22 Limit 6.9 A
    04:23 Limit 7.3 A
    04:23 Limit 7.7 A
    04:24 Limit 8.1 A
    04:25 Limit 8.4 A
    04:25 Limit 8.8 A
    04:30 Limit 6.6 A
    04:34 Limit 6.2 A
    04:42 Limit 6.6 A
    04:42 Limit 6.9 A
    04:43 Limit 7.3 A
    04:43 Limit 7.7 A
    04:44 Limit 8.1 A
    04:45 Limit 8.4 A
    04:45 Limit 8.8 A
    04:50 Limit 6.6 A
    04:54 Limit 6.2 A
    05:02 Limit 6.6 A
    05:02 Limit 6.9 A
    05:03 Limit 7.3 A
    05:03 Limit 7.7 A
    05:04 Limit 8.1 A
    05:05 Limit 8.4 A
    05:05 Limit 8.8 A
    05:10 Limit 6.6 A
    05:14 Limit 6.2 A
    05:22 Limit 6.6 A
    05:22 Limit 6.9 A
    05:23 Limit 7.3 A
    05:23 Limit 7.7 A
    05:24 Limit 8.1 A
    05:25 Limit 8.4 A
    05:25 Limit 8.8 A
    05:30 Limit 6.6 A
    05:34 Limit 6.2 A
    05:42 Limit 6.6 A
    05:42 Limit 6.9 A
    05:43 Limit 7.3 A
    05:43 Limit 7.7 A
    05:44 Limit 8.1 A
    05:45 Limit 8.4 A
    05:45 Limit 8.8 A
    05:50 Limit 6.6 A
    05:54 Limit 6.2 A
    06:02 Limit 6.6 A
    06:02 Limit 6.9 A
    06:03 Limit 7.3 A
    06:03 Limit 7.7 A
    06:04 Limit 8.1 A
    06:05 Limit 8.4 A
    06:05 Limit 8.8 A
    06:10 Limit 6.6 A
    06:14 Limit 6.2 A
    06:22 Limit 6.6 A
    06:22 Limit 6.9 A
    06:23 Limit 7.3 A
    06:23 Limit 7.7 A
    06:24 Limit 8.1 A
    06:25 Limit 8.4 A
    06:25 Limit 8.8 A
    06:30 Limit 6.6 A
    06:34 Limit 6.2 A
    06:42 Limit 6.6 A
    06:42 Limit 6.9 A
    06:43 Limit 7.3 A
    06:43 Limit 7.7 A
    06:44 Limit 8.1 A
    06:45 Limit 8.4 A
    06:45 Limit 8.8 A
    06:50 Limit 6.6 A
    06:54 Limit 6.2 A
    07:02 Limit 6.6 A
    07:02 Limit 6.9 A
    07:03 Limit 7.3 A
    07:03 Limit 7.7 A
    07:04 Limit 8.1 A
    07:05 Limit 8.4 A
    07:05 Limit 8.8 A
    07:10 Limit 6.6 A
    07:13 Limit 6.0 A
    07:21 Limit 6.4 A
    07:22 Limit 6.8 A
    07:23 Limit 7.2 A
    07:23 Limit 7.6 A
    07:24 Limit 7.9 A
    07:24 Limit 8.3 A
    07:25 Limit 8.7 A
    07:26 Limit 9.0 A
    07:30 Limit 6.0 A
    07:41 Limit 6.4 A
    07:42 Limit 6.8 A
    07:43 Limit 7.2 A
    07:43 Limit 7.6 A
    07:44 Limit 7.9 A
    07:44 Limit 8.3 A
    07:45 Limit 8.7 A
    07:46 Limit 9.0 A
    07:50 Limit 6.0 A

  Geladen: 36.9 kWh | Netzbezug: 0.05 kWh | Boost verbraucht: 5.00 kWh | SOC 90 % -> 99 %
```

### 13. wie Test 1, aber Wallbox meldet keine Ladeleistung

> Erwartet: identisch zu Test 1. Ohne Schätzung aus dem Limit hielte der Regler die eigene Ladung für einen PV-Einbruch und liefe in einen Stopp-Start-Kreisel.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.2    0.0 A   ·      +836 W       +0 W   52.1 %
  00:30    2.3    0.0 A   ·     +1617 W       +0 W   58.3 %
  00:45    3.4    0.0 A   ·     +2345 W       +0 W   68.2 %
  01:00    4.4    0.0 A   ·     +2500 W     +519 W   80.6 %
  01:15    5.3    0.0 A   ·     +2500 W    +1139 W   93.1 %
  01:30    6.1    0.0 A   ·        +0 W    +4205 W  100.0 %
  01:45    6.8    6.6 A  ⚡        +0 W     +163 W  100.0 %
  02:00    7.5    6.9 A  ⚡        +0 W     +414 W  100.0 %
  02:15    8.1    7.7 A  ⚡        +0 W     +266 W  100.0 %
  02:30    8.6    8.4 A  ⚡        +0 W     +134 W  100.0 %
  02:45    9.0    8.8 A  ⚡        +0 W     +154 W  100.0 %
  03:00    9.4    9.2 A  ⚡        +0 W     +121 W  100.0 %
  03:15    9.6    9.5 A  ⚡        +0 W     +102 W  100.0 %
  03:30    9.8    9.5 A  ⚡        +0 W     +237 W  100.0 %
  03:45   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:00   10.0    9.8 A  ⚡        +0 W     +138 W  100.0 %
  04:15   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:30    9.8    9.8 A  ⚡        +0 W      +30 W  100.0 %
  04:45    9.6    9.8 A  ⚡      -105 W       +0 W   99.8 %
  05:00    9.4    9.5 A  ⚡       -86 W       +0 W   99.3 %
  05:15    9.0    9.2 A  ⚡      -122 W       +0 W   98.8 %
  05:30    8.6    8.8 A  ⚡      -142 W       +0 W   97.9 %
  05:45    8.1    8.2 A  ⚡       -79 W       +0 W   97.1 %
  06:00    7.5    7.8 A  ⚡      -207 W       +0 W   96.2 %
  06:15    6.8    7.1 A  ⚡      -182 W       +0 W   95.2 %
  06:30    6.1    6.3 A  ⚡      -142 W       +0 W   93.9 %
  06:45    5.3    6.3 A  ⚡      -708 W       +0 W   91.8 %
  07:00    4.4    6.3 A  ⚡     -1328 W       +0 W   86.7 %
  07:15    3.4    6.3 A   ·     +2345 W       +0 W   81.3 %
  07:30    2.3    6.3 A   ·     +1617 W       +0 W   91.2 %
  07:45    1.2    6.3 A   ·      +836 W       +0 W   97.4 %

  Ereignisse:
    01:43 Limit 6.6 A
    01:43 START
    01:51 Limit 6.9 A
    02:00 Limit 7.3 A
    02:10 Limit 7.7 A
    02:20 Limit 8.1 A
    02:29 Limit 8.4 A
    02:41 Limit 8.8 A
    02:57 Limit 9.2 A
    03:11 Limit 9.5 A
    03:31 Limit 9.8 A
    04:52 Limit 9.5 A
    05:08 Limit 9.2 A
    05:24 Limit 8.8 A
    05:34 Limit 8.5 A
    05:43 Limit 8.2 A
    05:55 Limit 7.8 A
    06:05 Limit 7.4 A
    06:12 Limit 7.1 A
    06:20 Limit 6.7 A
    06:29 Limit 6.3 A
    07:13 STOPP

  Geladen: 31.9 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 1.01 kWh | SOC 50 % -> 99 %
```

### 14. wie Test 1, aber Ladeleistung eingefroren (letzte Meldung 5 min alt)

> Erwartet: identisch zu Test 1 — die tote Messung wird nach 30 s verworfen.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.2    0.0 A   ·      +836 W       +0 W   52.1 %
  00:30    2.3    0.0 A   ·     +1617 W       +0 W   58.3 %
  00:45    3.4    0.0 A   ·     +2345 W       +0 W   68.2 %
  01:00    4.4    0.0 A   ·     +2500 W     +519 W   80.6 %
  01:15    5.3    0.0 A   ·     +2500 W    +1139 W   93.1 %
  01:30    6.1    0.0 A   ·        +0 W    +4205 W  100.0 %
  01:45    6.8    6.6 A  ⚡        +0 W     +163 W  100.0 %
  02:00    7.5    6.9 A  ⚡        +0 W     +414 W  100.0 %
  02:15    8.1    7.7 A  ⚡        +0 W     +266 W  100.0 %
  02:30    8.6    8.4 A  ⚡        +0 W     +134 W  100.0 %
  02:45    9.0    8.8 A  ⚡        +0 W     +154 W  100.0 %
  03:00    9.4    9.2 A  ⚡        +0 W     +121 W  100.0 %
  03:15    9.6    9.5 A  ⚡        +0 W     +102 W  100.0 %
  03:30    9.8    9.5 A  ⚡        +0 W     +237 W  100.0 %
  03:45   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:00   10.0    9.8 A  ⚡        +0 W     +138 W  100.0 %
  04:15   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:30    9.8    9.8 A  ⚡        +0 W      +30 W  100.0 %
  04:45    9.6    9.8 A  ⚡      -105 W       +0 W   99.8 %
  05:00    9.4    9.5 A  ⚡       -86 W       +0 W   99.3 %
  05:15    9.0    9.2 A  ⚡      -122 W       +0 W   98.8 %
  05:30    8.6    8.8 A  ⚡      -142 W       +0 W   97.9 %
  05:45    8.1    8.2 A  ⚡       -79 W       +0 W   97.1 %
  06:00    7.5    7.8 A  ⚡      -207 W       +0 W   96.2 %
  06:15    6.8    7.1 A  ⚡      -182 W       +0 W   95.2 %
  06:30    6.1    6.3 A  ⚡      -142 W       +0 W   93.9 %
  06:45    5.3    6.3 A  ⚡      -708 W       +0 W   91.8 %
  07:00    4.4    6.3 A  ⚡     -1328 W       +0 W   86.7 %
  07:15    3.4    6.3 A   ·     +2345 W       +0 W   81.3 %
  07:30    2.3    6.3 A   ·     +1617 W       +0 W   91.2 %
  07:45    1.2    6.3 A   ·      +836 W       +0 W   97.4 %

  Ereignisse:
    01:43 Limit 6.6 A
    01:43 START
    01:51 Limit 6.9 A
    02:00 Limit 7.3 A
    02:10 Limit 7.7 A
    02:20 Limit 8.1 A
    02:29 Limit 8.4 A
    02:41 Limit 8.8 A
    02:57 Limit 9.2 A
    03:11 Limit 9.5 A
    03:31 Limit 9.8 A
    04:52 Limit 9.5 A
    05:08 Limit 9.2 A
    05:24 Limit 8.8 A
    05:34 Limit 8.5 A
    05:43 Limit 8.2 A
    05:55 Limit 7.8 A
    06:05 Limit 7.4 A
    06:12 Limit 7.1 A
    06:20 Limit 6.7 A
    06:29 Limit 6.3 A
    07:13 STOPP

  Geladen: 31.9 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 1.01 kWh | SOC 50 % -> 99 %
```

### 15. Wallbox meldet Ladung, Auto nimmt nichts an

> Erwartet: Stopp über den minpv-Timeout. Ohne den Deckel aus der PV-Erzeugung bliebe die Ladung offen, weil die geschätzte Leistung einen Überschuss vortäuscht.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.2    0.0 A   ·      +836 W       +0 W   52.1 %
  00:30    2.3    0.0 A   ·     +1617 W       +0 W   58.3 %
  00:45    3.4    0.0 A   ·     +2345 W       +0 W   68.2 %
  01:00    4.4    0.0 A   ·     +2500 W     +519 W   80.6 %
  01:15    5.3    0.0 A   ·     +2500 W    +1139 W   93.1 %
  01:30    6.1    0.0 A   ·        +0 W    +4205 W  100.0 %
  01:45    6.8    6.6 A  ⚡        +0 W    +4717 W  100.0 %
  02:00    7.5    7.7 A  ⚡        +0 W    +5175 W  100.0 %
  02:15    8.1    8.4 A  ⚡        +0 W    +5579 W  100.0 %
  02:30    8.6    8.8 A  ⚡        +0 W    +5930 W  100.0 %
  02:45    9.0    9.2 A  ⚡        +0 W    +6226 W  100.0 %
  03:00    9.4    9.8 A  ⚡        +0 W    +6469 W  100.0 %
  03:15    9.6    9.8 A  ⚡        +0 W    +6657 W  100.0 %
  03:30    9.8   10.2 A  ⚡        +0 W    +6792 W  100.0 %
  03:45   10.0   10.5 A  ⚡        +0 W    +6873 W  100.0 %
  04:00   10.0   10.5 A  ⚡        +0 W    +6900 W  100.0 %
  04:15   10.0   10.5 A  ⚡        +0 W    +6873 W  100.0 %
  04:30    9.8   10.5 A  ⚡        +0 W    +6792 W  100.0 %
  04:45    9.6   10.5 A  ⚡        +0 W    +6657 W  100.0 %
  05:00    9.4   10.2 A  ⚡        +0 W    +6469 W  100.0 %
  05:15    9.0    9.8 A  ⚡        +0 W    +6226 W  100.0 %
  05:30    8.6    9.5 A  ⚡        +0 W    +5930 W  100.0 %
  05:45    8.1    8.8 A  ⚡        +0 W    +5579 W  100.0 %
  06:00    7.5    8.2 A  ⚡        +0 W    +5175 W  100.0 %
  06:15    6.8    7.8 A  ⚡        +0 W    +4717 W  100.0 %
  06:30    6.1    7.1 A  ⚡        +0 W    +4205 W  100.0 %
  06:45    5.3    6.3 A  ⚡        +0 W    +3639 W  100.0 %
  07:00    4.4    6.3 A  ⚡        +0 W    +3019 W  100.0 %
  07:15    3.4    6.3 A  ⚡        +0 W    +2345 W  100.0 %
  07:30    2.3    6.3 A   ·        +0 W    +1617 W  100.0 %
  07:45    1.2    6.3 A   ·        +0 W     +836 W  100.0 %

  Ereignisse:
    01:43 Limit 6.6 A
    01:43 START
    01:46 Limit 6.9 A
    01:50 Limit 7.3 A
    01:56 Limit 7.7 A
    02:05 Limit 8.1 A
    02:13 Limit 8.4 A
    02:23 Limit 8.8 A
    02:35 Limit 9.2 A
    02:46 Limit 9.5 A
    02:58 Limit 9.8 A
    03:18 Limit 10.2 A
    03:43 Limit 10.5 A
    04:45 Limit 10.2 A
    05:07 Limit 9.8 A
    05:20 Limit 9.5 A
    05:30 Limit 9.2 A
    05:43 Limit 8.8 A
    05:51 Limit 8.5 A
    05:59 Limit 8.2 A
    06:09 Limit 7.8 A
    06:18 Limit 7.4 A
    06:24 Limit 7.1 A
    06:32 Limit 6.7 A
    06:40 Limit 6.3 A
    07:22 STOPP

  Geladen: 0.0 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.00 kWh | SOC 50 % -> 100 %
```

### 16. Auto nimmt nur 6 A, Wallbox meldet keine Ladeleistung

> Erwartet: Limit bleibt in der Nähe dessen, was das Auto zieht, statt bis 16 A hochzulaufen — der Deckel aus PV minus Netz minus Batterie hält es fest.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.2    0.0 A   ·      +836 W       +0 W   52.1 %
  00:30    2.3    0.0 A   ·     +1617 W       +0 W   58.3 %
  00:45    3.4    0.0 A   ·     +2345 W       +0 W   68.2 %
  01:00    4.4    0.0 A   ·     +2500 W     +519 W   80.6 %
  01:15    5.3    0.0 A   ·     +2500 W    +1139 W   93.1 %
  01:30    6.1    0.0 A   ·        +0 W    +4205 W  100.0 %
  01:45    6.8    6.6 A  ⚡        +0 W     +577 W  100.0 %
  02:00    7.5    7.7 A  ⚡        +0 W    +1035 W  100.0 %
  02:15    8.1    8.4 A  ⚡        +0 W    +1439 W  100.0 %
  02:30    8.6    8.8 A  ⚡        +0 W    +1790 W  100.0 %
  02:45    9.0    9.2 A  ⚡        +0 W    +2086 W  100.0 %
  03:00    9.4    9.8 A  ⚡        +0 W    +2329 W  100.0 %
  03:15    9.6    9.8 A  ⚡        +0 W    +2517 W  100.0 %
  03:30    9.8   10.2 A  ⚡        +0 W    +2652 W  100.0 %
  03:45   10.0   10.5 A  ⚡        +0 W    +2733 W  100.0 %
  04:00   10.0   10.5 A  ⚡        +0 W    +2760 W  100.0 %
  04:15   10.0   10.5 A  ⚡        +0 W    +2733 W  100.0 %
  04:30    9.8   10.5 A  ⚡        +0 W    +2652 W  100.0 %
  04:45    9.6   10.5 A  ⚡        +0 W    +2517 W  100.0 %
  05:00    9.4   10.2 A  ⚡        +0 W    +2329 W  100.0 %
  05:15    9.0    9.8 A  ⚡        +0 W    +2086 W  100.0 %
  05:30    8.6    9.5 A  ⚡        +0 W    +1790 W  100.0 %
  05:45    8.1    8.8 A  ⚡        +0 W    +1439 W  100.0 %
  06:00    7.5    8.2 A  ⚡        +0 W    +1035 W  100.0 %
  06:15    6.8    7.8 A  ⚡        +0 W     +577 W  100.0 %
  06:30    6.1    7.1 A  ⚡        +0 W      +65 W  100.0 %
  06:45    5.3    6.3 A  ⚡      -501 W       +0 W   98.9 %
  07:00    4.4    6.3 A  ⚡     -1121 W       +0 W   94.9 %
  07:15    3.4    6.3 A  ⚡     -1795 W       +0 W   87.6 %
  07:30    2.3    6.3 A   ·     +1617 W       +0 W   93.6 %
  07:45    1.2    6.3 A   ·      +836 W       +0 W   99.7 %

  Ereignisse:
    01:43 Limit 6.6 A
    01:43 START
    01:46 Limit 6.9 A
    01:50 Limit 7.3 A
    01:56 Limit 7.7 A
    02:05 Limit 8.1 A
    02:13 Limit 8.4 A
    02:23 Limit 8.8 A
    02:35 Limit 9.2 A
    02:46 Limit 9.5 A
    02:58 Limit 9.8 A
    03:18 Limit 10.2 A
    03:43 Limit 10.5 A
    04:45 Limit 10.2 A
    05:07 Limit 9.8 A
    05:20 Limit 9.5 A
    05:30 Limit 9.2 A
    05:43 Limit 8.8 A
    05:51 Limit 8.5 A
    05:59 Limit 8.2 A
    06:09 Limit 7.8 A
    06:18 Limit 7.4 A
    06:24 Limit 7.1 A
    06:32 Limit 6.7 A
    06:40 Limit 6.3 A
    07:17 STOPP

  Geladen: 23.1 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.71 kWh | SOC 50 % -> 100 %
```

### 17. Box lehnt Start ab (hängt in Finishing)

> Erwartet: ein abgelehnter Versuch, dann ChangeAvailability-Zyklus, danach Start. Ohne Backoff liefe der RemoteStart im 5-Sekunden-Takt weiter.

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.2    0.0 A   ·      +836 W       +0 W   52.1 %
  00:30    2.3    0.0 A   ·     +1617 W       +0 W   58.3 %
  00:45    3.4    0.0 A   ·     +2345 W       +0 W   68.2 %
  01:00    4.4    0.0 A   ·     +2500 W     +519 W   80.6 %
  01:15    5.3    0.0 A   ·     +2500 W    +1139 W   93.1 %
  01:30    6.1    0.0 A   ·        +0 W    +4205 W  100.0 %
  01:45    6.8    6.7 A  ⚡        +0 W      +94 W  100.0 %
  02:00    7.5    7.1 A  ⚡        +0 W     +276 W  100.0 %
  02:15    8.1    7.8 A  ⚡        +0 W     +197 W  100.0 %
  02:30    8.6    8.2 A  ⚡        +0 W     +272 W  100.0 %
  02:45    9.0    8.8 A  ⚡        +0 W     +154 W  100.0 %
  03:00    9.4    9.2 A  ⚡        +0 W     +121 W  100.0 %
  03:15    9.6    9.5 A  ⚡        +0 W     +102 W  100.0 %
  03:30    9.8    9.5 A  ⚡        +0 W     +237 W  100.0 %
  03:45   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:00   10.0    9.8 A  ⚡        +0 W     +138 W  100.0 %
  04:15   10.0    9.8 A  ⚡        +0 W     +111 W  100.0 %
  04:30    9.8    9.8 A  ⚡        +0 W      +30 W  100.0 %
  04:45    9.6    9.8 A  ⚡      -105 W       +0 W   99.8 %
  05:00    9.4    9.5 A  ⚡       -86 W       +0 W   99.3 %
  05:15    9.0    9.2 A  ⚡      -122 W       +0 W   98.8 %
  05:30    8.6    8.8 A  ⚡      -142 W       +0 W   97.9 %
  05:45    8.1    8.2 A  ⚡       -79 W       +0 W   97.1 %
  06:00    7.5    7.8 A  ⚡      -207 W       +0 W   96.2 %
  06:15    6.8    7.1 A  ⚡      -182 W       +0 W   95.2 %
  06:30    6.1    6.3 A  ⚡      -142 W       +0 W   93.9 %
  06:45    5.3    6.3 A  ⚡      -708 W       +0 W   91.8 %
  07:00    4.4    6.3 A  ⚡     -1328 W       +0 W   86.7 %
  07:15    3.4    6.3 A   ·     +2345 W       +0 W   81.3 %
  07:30    2.3    6.3 A   ·     +1617 W       +0 W   91.2 %
  07:45    1.2    6.3 A   ·      +836 W       +0 W   97.4 %

  Ereignisse:
    01:43 Limit 6.6 A
    01:43 START abgelehnt
    01:43 ChangeAvailability-Zyklus
    01:43 Limit 6.7 A
    01:43 START
    01:55 Limit 7.1 A
    02:02 Limit 7.4 A
    02:12 Limit 7.8 A
    02:23 Limit 8.2 A
    02:32 Limit 8.5 A
    02:41 Limit 8.8 A
    02:57 Limit 9.2 A
    03:11 Limit 9.5 A
    03:31 Limit 9.8 A
    04:52 Limit 9.5 A
    05:08 Limit 9.2 A
    05:24 Limit 8.8 A
    05:34 Limit 8.5 A
    05:43 Limit 8.2 A
    05:55 Limit 7.8 A
    06:05 Limit 7.4 A
    06:12 Limit 7.1 A
    06:20 Limit 6.7 A
    06:29 Limit 6.3 A
    07:13 STOPP

  Geladen: 31.9 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 1.01 kWh | SOC 50 % -> 99 %
```

### 18. Fahrzeug voll (SuspendedEV), Box nimmt keinen Start an

> Erwartet: wenige Startversuche über 8 h statt Dauerfunk — hier 56. Ohne Bremse wären es rund 4000 (alle 5 s).

```
  Zeit   PV_A  Limit  lädt   Batterie      Netz    SOC
  00:00    0.0    0.0 A   ·        +0 W       +0 W   50.0 %
  00:15    1.2    0.0 A   ·      +836 W       +0 W   52.1 %
  00:30    2.3    0.0 A   ·     +1617 W       +0 W   58.3 %
  00:45    3.4    0.0 A   ·     +2345 W       +0 W   68.2 %
  01:00    4.4    0.0 A   ·     +2500 W     +519 W   80.6 %
  01:15    5.3    0.0 A   ·     +2500 W    +1139 W   93.1 %
  01:30    6.1    0.0 A   ·        +0 W    +4205 W  100.0 %
  01:45    6.8    6.6 A   ·        +0 W    +4717 W  100.0 %
  02:00    7.5    7.3 A   ·        +0 W    +5175 W  100.0 %
  02:15    8.1    7.9 A   ·        +0 W    +5579 W  100.0 %
  02:30    8.6    8.5 A   ·        +0 W    +5930 W  100.0 %
  02:45    9.0    8.9 A   ·        +0 W    +6226 W  100.0 %
  03:00    9.4    9.3 A   ·        +0 W    +6469 W  100.0 %
  03:15    9.6    9.6 A   ·        +0 W    +6657 W  100.0 %
  03:30    9.8    9.8 A   ·        +0 W    +6792 W  100.0 %
  03:45   10.0    9.9 A   ·        +0 W    +6873 W  100.0 %
  04:00   10.0    9.9 A   ·        +0 W    +6900 W  100.0 %
  04:15   10.0    9.9 A   ·        +0 W    +6873 W  100.0 %
  04:30    9.8    9.8 A   ·        +0 W    +6792 W  100.0 %
  04:45    9.6    9.6 A   ·        +0 W    +6657 W  100.0 %
  05:00    9.4    9.4 A   ·        +0 W    +6469 W  100.0 %
  05:15    9.0    9.0 A   ·        +0 W    +6226 W  100.0 %
  05:30    8.6    8.6 A   ·        +0 W    +5930 W  100.0 %
  05:45    8.1    8.1 A   ·        +0 W    +5579 W  100.0 %
  06:00    7.5    7.5 A   ·        +0 W    +5175 W  100.0 %
  06:15    6.8    6.9 A   ·        +0 W    +4717 W  100.0 %
  06:30    6.1    6.6 A   ·        +0 W    +4205 W  100.0 %
  06:45    5.3    6.6 A   ·        +0 W    +3639 W  100.0 %
  07:00    4.4    6.6 A   ·        +0 W    +3019 W  100.0 %
  07:15    3.4    6.6 A   ·        +0 W    +2345 W  100.0 %
  07:30    2.3    6.6 A   ·        +0 W    +1617 W  100.0 %
  07:45    1.2    6.6 A   ·        +0 W     +836 W  100.0 %

  Ereignisse:
    01:43 Limit 6.6 A
    01:43 START abgelehnt
    01:48 Limit 6.9 A
    01:48 START abgelehnt
    01:53 Limit 7.1 A
    01:53 START abgelehnt
    01:58 Limit 7.3 A
    01:58 START abgelehnt
    02:03 Limit 7.5 A
    02:03 START abgelehnt
    02:08 Limit 7.7 A
    02:08 START abgelehnt
    02:13 Limit 7.9 A
    02:13 START abgelehnt
    02:18 Limit 8.1 A
    02:18 START abgelehnt
    02:23 Limit 8.3 A
    02:23 START abgelehnt
    02:28 Limit 8.5 A
    02:28 START abgelehnt
    02:33 Limit 8.6 A
    02:33 START abgelehnt
    02:38 Limit 8.8 A
    02:38 START abgelehnt
    02:43 Limit 8.9 A
    02:43 START abgelehnt
    02:48 Limit 9.0 A
    02:48 START abgelehnt
    02:53 Limit 9.1 A
    02:53 START abgelehnt
    02:58 Limit 9.3 A
    02:58 START abgelehnt
    03:03 Limit 9.4 A
    03:03 START abgelehnt
    03:08 Limit 9.5 A
    03:08 START abgelehnt
    03:13 Limit 9.6 A
    03:13 START abgelehnt
    03:18 Limit 9.6 A
    03:18 START abgelehnt
    03:23 Limit 9.7 A
    03:23 START abgelehnt
    03:28 Limit 9.8 A
    03:28 START abgelehnt
    03:33 Limit 9.8 A
    03:33 START abgelehnt
    03:38 Limit 9.9 A
    03:38 START abgelehnt
    03:43 Limit 9.9 A
    03:43 START abgelehnt
    03:48 Limit 9.9 A
    03:48 START abgelehnt
    03:53 Limit 9.9 A
    03:53 START abgelehnt
    03:58 Limit 9.9 A
    03:58 START abgelehnt
    04:03 Limit 9.9 A
    04:03 START abgelehnt
    04:08 Limit 9.9 A
    04:08 START abgelehnt
    04:13 Limit 9.9 A
    04:13 START abgelehnt
    04:18 Limit 9.9 A
    04:18 START abgelehnt
    04:23 Limit 9.9 A
    04:23 START abgelehnt
    04:28 Limit 9.8 A
    04:28 START abgelehnt
    04:33 Limit 9.8 A
    04:33 START abgelehnt
    04:38 Limit 9.7 A
    04:38 START abgelehnt
    04:43 Limit 9.6 A
    04:43 START abgelehnt
    04:48 Limit 9.5 A
    04:48 START abgelehnt
    04:53 Limit 9.5 A
    04:53 START abgelehnt
    04:58 Limit 9.4 A
    04:58 START abgelehnt
    05:03 Limit 9.3 A
    05:03 START abgelehnt
    05:08 Limit 9.1 A
    05:08 START abgelehnt
    05:13 Limit 9.0 A
    05:13 START abgelehnt
    05:18 Limit 8.9 A
    05:18 START abgelehnt
    05:23 Limit 8.8 A
    05:23 START abgelehnt
    05:28 Limit 8.6 A
    05:28 START abgelehnt
    05:33 Limit 8.4 A
    05:33 START abgelehnt
    05:38 Limit 8.3 A
    05:38 START abgelehnt
    05:43 Limit 8.1 A
    05:43 START abgelehnt
    05:48 Limit 7.9 A
    05:48 START abgelehnt
    05:53 Limit 7.7 A
    05:53 START abgelehnt
    05:58 Limit 7.5 A
    05:58 START abgelehnt
    06:03 Limit 7.3 A
    06:03 START abgelehnt
    06:08 Limit 7.1 A
    06:08 START abgelehnt
    06:13 Limit 6.9 A
    06:13 START abgelehnt
    06:18 Limit 6.6 A
    06:18 START abgelehnt

  Geladen: 0.0 kWh | Netzbezug: 0.00 kWh | Boost verbraucht: 0.00 kWh | SOC 50 % -> 100 %
```

