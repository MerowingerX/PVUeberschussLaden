#!/usr/bin/env python3
"""M1: Verifiziert die Batterie-Steuerregister (LUNA2000) — nur lesen.

Liest die Kandidaten-Register für Zwangsladung/Netzladung einzeln und gibt
Rohwert plus Interpretation aus. Erwartung im Ruhezustand (keine Zwangsladung
aktiv): Befehlsregister = 0 (Stop), SOC-/Leistungswerte in plausiblen Bereichen.
Passt das Gelesene nicht zur Doku, ist das Mapping falsch — dann NICHT schreiben.

Zusätzlich ein Roh-Scan über die umliegenden Bereiche, um die tatsächliche
Registerbelegung der Firmware zu sehen.

Aufruf:
    python read_battery_registers.py [<inverter-ip>] [--port 502] [--unit 1]
    (IP fällt zurück auf PVUEB_INVERTER_IP aus ../.env)
"""

import argparse
import asyncio
import os
import sys

from pymodbus.client import AsyncModbusTcpClient

# Register laut huawei_solar 3.0.6 (PyPI), am Gerät verifiziert 2026-07-16.
# (name, register, anzahl_worte, skalierung, einheit, plausibel_von, plausibel_bis)
CANDIDATES = [
    ("Charge from grid (an/aus)",        47087, 1, 1,   "",   0, 1),
    ("Grid charge cutoff SOC",           47088, 1, 0.1, "%",  0, 100),
    ("Charging cutoff capacity",         47081, 1, 0.1, "%",  0, 100),
    ("Discharging cutoff capacity",      47082, 1, 0.1, "%",  0, 100),
    ("Working mode (C: 2=SelbstVerbr.)", 47086, 1, 1,   "",   0, 5),
    ("Forced chg/dis: Dauer",            47083, 1, 1,   "min", 0, 1440),
    ("Forced chg/dis: Ist-Leistung",     47084, 2, 1,   "W",  0, 10000),
    ("Forcible chg/dis: Befehl",         47100, 1, 1,   "",   0, 2),
    ("Forcible chg/dis: Ziel-SOC",       47101, 1, 0.1, "%",  0, 100),
    ("Forcible Setting-Modus (0=Zeit,1=SOC)", 47246, 1, 1, "", 0, 1),
    ("Forcible charge power",            47247, 2, 1,   "W",  0, 10000),
    ("Forcible discharge power",         47249, 2, 1,   "W",  0, 10000),
    # Referenz zur Gegenprobe (bereits verifiziert):
    ("Batterie-SOC (verifiziert)",       37760, 1, 0.1, "%",  0, 100),
]

# Roh-Scan-Bereiche: (start, ende_inklusive)
SCAN_RANGES = [(47075, 47110), (47240, 47255)]


def decode_unsigned(words: list[int]) -> int:
    value = 0
    for word in words:
        value = (value << 16) | word
    return value


def load_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    try:
        with open(env_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        pass


async def read_one(client, register: int, count: int, unit: int):
    """Liest ein Register, gibt Wortliste oder Fehlertext zurück."""
    try:
        result = await client.read_holding_registers(register, count=count, device_id=unit)
        if result.isError():
            return None, str(result)
        return result.registers, None
    except Exception as exc:  # noqa: BLE001 - PoC: alles anzeigen
        return None, str(exc)


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", default=os.environ.get("PVUEB_INVERTER_IP"))
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    args = parser.parse_args()
    if not args.host:
        sys.exit("Wechselrichter-IP fehlt: Argument oder PVUEB_INVERTER_IP/.env setzen")

    client = AsyncModbusTcpClient(args.host, port=args.port)
    await client.connect()
    if not client.connected:
        sys.exit(f"Keine Verbindung zu {args.host}:{args.port}")

    print("Verbunden. Warte 3 s (SDongle-Eigenheit) ...")
    await asyncio.sleep(3)

    try:
        print("\n=== Kandidaten-Register ===")
        for name, register, count, scale, unit, lo, hi in CANDIDATES:
            words, err = await read_one(client, register, count, args.unit)
            if err:
                print(f"{register:5d} {name:35s} FEHLER: {err}")
                continue
            value = decode_unsigned(words) * scale
            flag = "ok" if lo <= value <= hi else "UNPLAUSIBEL"
            raw = " ".join(f"0x{w:04x}" for w in words)
            print(f"{register:5d} {name:35s} {value:10.1f} {unit:3s} [{raw}] {flag}")
            await asyncio.sleep(0.2)

        print("\n=== Roh-Scan ===")
        for start, end in SCAN_RANGES:
            for register in range(start, end + 1):
                words, err = await read_one(client, register, 1, args.unit)
                if err:
                    print(f"{register:5d}  --  ({err})")
                else:
                    print(f"{register:5d}  {words[0]:6d}  (0x{words[0]:04x})")
                await asyncio.sleep(0.1)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
