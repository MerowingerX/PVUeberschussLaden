#!/usr/bin/env python3
"""Aus einem Mitschnitt (record_status.py) eine PV-Kurve für test_sim.py machen.

    python curve_from_recording.py ../recordings/status-2026-07-18.jsonl
    python curve_from_recording.py <datei> --from 10:00 --to 18:00 --out kurve.json

Die Kurve ist der echte PV-Überschuss in Ampere über der Zeit in Minuten,
also genau das, was Sim(curve) erwartet. In test_sim.py:

    from curve_from_recording import load_curve
    sim = Sim(load_curve("../recordings/status-2026-07-18.json"), soc=50)
"""

import argparse
import datetime
import json

WPA = 3 * 230.0          # 690 W pro Ampere


def read_samples(path: str, start: str | None = None, end: str | None = None):
    """(Minute seit Beginn, Ampere) — Zeilen ohne Messwert werden übersprungen."""
    rows = []
    with open(path) as fh:
        for line in fh:
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            if s.get("pv_surplus_w") is None or "t" not in s:
                continue        # Modbus weg oder Abruf fehlgeschlagen
            t = datetime.datetime.fromisoformat(s["t"])
            hhmm = t.strftime("%H:%M")
            if (start and hhmm < start) or (end and hhmm > end):
                continue
            rows.append((t, s["pv_surplus_w"] / WPA))
    if not rows:
        raise SystemExit(f"{path}: keine verwertbaren Messwerte im gewählten Bereich")
    t0 = rows[0][0]
    return [((t - t0).total_seconds() / 60, round(a, 2)) for t, a in rows]


def load_curve(path: str, start: str | None = None, end: str | None = None):
    """Kurvenfunktion Minute -> Ampere, linear zwischen den Stützstellen."""
    points = read_samples(path, start, end) if path.endswith(".jsonl") else \
        [tuple(p) for p in json.load(open(path))["points"]]

    def curve(minute: float) -> float:
        if minute <= points[0][0]:
            return points[0][1]
        if minute >= points[-1][0]:
            return points[-1][1]
        lo, hi = 0, len(points) - 1
        while hi - lo > 1:                       # binäre Suche, Kurve ist sortiert
            mid = (lo + hi) // 2
            if points[mid][0] <= minute:
                lo = mid
            else:
                hi = mid
        (m0, a0), (m1, a1) = points[lo], points[hi]
        return a0 + (a1 - a0) * (minute - m0) / (m1 - m0) if m1 > m0 else a0

    curve.points = points
    return curve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--from", dest="start", help="Uhrzeit HH:MM")
    parser.add_argument("--to", dest="end", help="Uhrzeit HH:MM")
    parser.add_argument("--out", help="Kurve als JSON speichern")
    args = parser.parse_args()

    points = read_samples(args.recording, args.start, args.end)
    amps = [a for _, a in points]
    dauer = points[-1][0]
    print(f"{len(points)} Messwerte über {dauer/60:.1f} h")
    print(f"PV-Überschuss: min {min(amps):.1f} A | mittel {sum(amps)/len(amps):.1f} A "
          f"| max {max(amps):.1f} A")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"quelle": args.recording, "points": points}, fh)
        print(f"-> {args.out}")


if __name__ == "__main__":
    main()
