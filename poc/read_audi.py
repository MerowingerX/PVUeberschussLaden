#!/usr/bin/env python3
"""Zugang zur MyAudi-Cloud prüfen und den Fahrzeug-Snapshot anzeigen.

    python read_audi.py

Liest PVUEB_AUDI_* aus der .env, meldet sich über carconnectivity an und gibt
aus, was charge_loop.py auf dem Fahrzeug-Slide anzeigen würde.

Die VIN wird gekürzt ausgegeben, das Passwort nie. Der erste Login dauert
länger (OAuth-Fluss); danach liegt ein Token in poc/.audi-tokenstore.json.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import charge_loop as c  # noqa: E402


async def main() -> None:
    c.load_dotenv()
    c.state.audi_user = os.environ.get("PVUEB_AUDI_USER", "")
    c.state.audi_password = os.environ.get("PVUEB_AUDI_PASSWORD", "")
    c.state.audi_spin = os.environ.get("PVUEB_AUDI_SPIN", "")
    c.state.audi_vin = os.environ.get("PVUEB_AUDI_VIN", "")
    if not (c.state.audi_user and c.state.audi_password):
        sys.exit("PVUEB_AUDI_USER und PVUEB_AUDI_PASSWORD in .env eintragen")

    try:
        from carconnectivity.carconnectivity import CarConnectivity
    except ImportError:
        sys.exit("carconnectivity fehlt — pip install carconnectivity-connector-audi")

    config = {"carConnectivity": {"connectors": [{"type": "audi", "config": {
        "username": c.state.audi_user,
        "password": c.state.audi_password,
        "spin": c.state.audi_spin or None,
        "interval": c.AUDI_MIN_INTERVAL_S,
    }}]}}

    print(f"Melde an als {c.state.audi_user} … (erster Login dauert)")
    loop = asyncio.get_running_loop()
    car = await loop.run_in_executor(
        None, CarConnectivity, config, c.AUDI_TOKENSTORE, c.AUDI_CACHE)
    try:
        await loop.run_in_executor(None, car.startup)
        await loop.run_in_executor(None, car.fetch_all)
        fahrzeuge = car.get_garage().list_vehicles()
        if not fahrzeuge:
            sys.exit("Anmeldung ok, aber kein Fahrzeug im Konto")

        print(f"\n{len(fahrzeuge)} Fahrzeug(e) im Konto:")
        for fahrzeug in fahrzeuge:
            vin = str(c.attr_value(fahrzeug.vin) or "?")
            print(f"  …{vin[-4:]}  {c.attr_value(fahrzeug.name) or '?'}"
                  + (f"   → PVUEB_AUDI_VIN={vin}" if len(fahrzeuge) > 1 else ""))

        schnappschuss = c.audi_snapshot(fahrzeuge[0])
        print("\nWas das Fahrzeug-Slide zeigen würde:")
        for schluessel, wert in schnappschuss.items():
            print(f"  {schluessel:16s} {wert}")
    finally:
        try:
            await loop.run_in_executor(None, car.shutdown)
        except Exception:  # noqa: BLE001 — beim Aufräumen ist alles verzeihlich
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
