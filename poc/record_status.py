#!/usr/bin/env python3
"""Live-Status der laufenden Instanz mitschreiben — Rohmaterial für Testfälle.

    python record_status.py [--url http://…/api/status] [--interval 10]
                            [--dir ../recordings]

Schreibt eine JSON-Zeile pro Abtastung nach <dir>/status-JJJJ-MM-TT.jsonl,
Tageswechsel legt automatisch eine neue Datei an. Fehlversuche landen als
{"t": …, "error": …} in derselben Datei, damit Lücken sichtbar bleiben.

Aus den Aufzeichnungen macht curve_from_recording.py PV-Kurven für test_sim.py.
"""

import argparse
import datetime
import json
import os
import time
import urllib.request

DEFAULT_URL = "http://192.168.100.2:8080/api/status"
# Der Regler tastet alle 5 s; 10 s reichen, um Wolkenlöcher sauber abzubilden
DEFAULT_INTERVAL = 10


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("PVUEB_STATUS_URL", DEFAULT_URL))
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--dir", default=os.path.join(here, os.pardir, "recordings"))
    args = parser.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    day, fh = None, None
    try:
        while True:
            now = datetime.datetime.now()
            if now.date() != day:                      # Tageswechsel: neue Datei
                if fh:
                    fh.close()
                day = now.date()
                path = os.path.join(args.dir, f"status-{day.isoformat()}.jsonl")
                fh = open(path, "a", buffering=1)
                print(f"schreibe {path}", flush=True)
            try:
                with urllib.request.urlopen(args.url, timeout=5) as response:
                    sample = json.load(response)
            except Exception as exc:                   # noqa: BLE001 — Lücke protokollieren, weiterlaufen
                sample = {"error": str(exc)}
            sample["t"] = now.isoformat(timespec="seconds")
            fh.write(json.dumps(sample) + "\n")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if fh:
            fh.close()


if __name__ == "__main__":
    main()
