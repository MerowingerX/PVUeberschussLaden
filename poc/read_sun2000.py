#!/usr/bin/env python3
"""M1-PoC: Liest Netzleistung, PV-Leistung und Batterie-SOC vom Sun2000.

Aufruf:
    python read_sun2000.py <inverter-ip> [--port 502] [--unit 1] [--interval 5]

Werte gegen die FusionSolar-App vergleichen, insbesondere das Vorzeichen
der Netzleistung (Register 37113): Erwartung positiv = Einspeisung.
"""

import argparse
import asyncio
import datetime
import sys

from pymodbus.client import AsyncModbusTcpClient


# (name, register, anzahl_worte, skalierung, einheit)
REGISTERS = [
    ("PV-Leistung", 32080, 2, 1, "W"),
    ("Netzleistung (Power Sensor)", 37113, 2, 1, "W"),
    ("Batterie-Leistung", 37001, 2, 1, "W"),
    ("Batterie-SOC", 37760, 1, 0.1, "%"),
]


def decode_signed(words: list[int]) -> int:
    value = 0
    for word in words:
        value = (value << 16) | word
    bits = 16 * len(words)
    if value >= 1 << (bits - 1):
        value -= 1 << bits
    return value


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IP des SDongle/Wechselrichters")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1, help="Modbus Unit-ID (auch 0 oder 100 probieren)")
    parser.add_argument("--interval", type=float, default=5.0, help="Polling-Intervall in Sekunden")
    args = parser.parse_args()

    client = AsyncModbusTcpClient(args.host, port=args.port)
    await client.connect()
    if not client.connected:
        sys.exit(f"Keine Verbindung zu {args.host}:{args.port}")

    # Der SDongle braucht nach dem TCP-Connect einige Sekunden, bevor er
    # Modbus-Anfragen beantwortet, sonst kommen Timeouts/ExceptionResponses.
    print("Verbunden. Warte 3 s (SDongle-Eigenheit) ...")
    await asyncio.sleep(3)

    try:
        while True:
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"--- {stamp} ---")
            for name, register, count, scale, unit in REGISTERS:
                try:
                    result = await client.read_holding_registers(
                        register, count=count, device_id=args.unit
                    )
                    if result.isError():
                        print(f"{name:30s} FEHLER: {result}")
                        continue
                    value = decode_signed(result.registers) * scale
                    print(f"{name:30s} {value:10.1f} {unit}")
                except Exception as exc:  # noqa: BLE001 - PoC: alles anzeigen
                    print(f"{name:30s} EXCEPTION: {exc}")
            await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
