#!/usr/bin/env python3
"""Zugang zur myWallbox-Cloud prüfen und die Charger-ID ermitteln.

    python read_wallbox_cloud.py [--watch]

Liest PVUEB_WALLBOX_USER und PVUEB_WALLBOX_PASSWORD aus der .env, meldet sich
an, listet die Boxen des Kontos samt ID und zeigt die aktuellen Werte. Mit
--watch wird alle 30 s neu abgefragt, bis Strg-C.

Das Passwort wird nie ausgegeben. Die ermittelte ID gehört als
PVUEB_WALLBOX_ID in die .env, damit charge_loop.py sie mitschreibt.
"""

import argparse
import asyncio
import base64
import datetime
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from charge_loop import load_dotenv  # noqa: E402

API = "https://api.wall-box.com"
KOPF = {"Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=utf-8"}


async def anmelden(sitzung: aiohttp.ClientSession, user: str, passwort: str) -> str:
    anmeldung = base64.b64encode(f"{user}:{passwort}".encode()).decode()
    async with sitzung.post(f"{API}/auth/token/user",
                            headers={**KOPF, "Authorization": f"Basic {anmeldung}"},
                            timeout=aiohttp.ClientTimeout(total=20)) as antwort:
        if antwort.status in (401, 403):
            sys.exit(f"Anmeldung abgelehnt (HTTP {antwort.status}) — "
                     "Benutzer oder Passwort stimmen nicht")
        antwort.raise_for_status()
        return (await antwort.json())["jwt"]


async def boxen(sitzung: aiohttp.ClientSession, token: str) -> list[dict]:
    """Alle Ladepunkte des Kontos — hier steht die Charger-ID drin."""
    async with sitzung.get(f"{API}/v3/chargers/groups",
                           headers={**KOPF, "Authorization": f"Bearer {token}"},
                           timeout=aiohttp.ClientTimeout(total=20)) as antwort:
        antwort.raise_for_status()
        daten = await antwort.json()
    gefunden = []
    for gruppe in daten.get("result", {}).get("groups", []):
        gefunden.extend(gruppe.get("chargers", []))
    return gefunden


async def status(sitzung: aiohttp.ClientSession, token: str, charger_id) -> dict:
    async with sitzung.get(f"{API}/chargers/status/{charger_id}",
                           headers={**KOPF, "Authorization": f"Bearer {token}"},
                           timeout=aiohttp.ClientTimeout(total=20)) as antwort:
        antwort.raise_for_status()
        return await antwort.json()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="alle 30 s wiederholen")
    args = parser.parse_args()

    load_dotenv()
    user = os.environ.get("PVUEB_WALLBOX_USER", "")
    passwort = os.environ.get("PVUEB_WALLBOX_PASSWORD", "")
    if not (user and passwort):
        sys.exit("PVUEB_WALLBOX_USER und PVUEB_WALLBOX_PASSWORD in .env eintragen")

    async with aiohttp.ClientSession() as sitzung:
        token = await anmelden(sitzung, user, passwort)
        print(f"Angemeldet als {user}\n")

        liste = await boxen(sitzung, token)
        if not liste:
            sys.exit("Keine Ladepunkte im Konto gefunden")
        print("Ladepunkte im Konto:")
        for box in liste:
            print(f"  ID {box.get('id')}  {box.get('name', '?')}  "
                  f"({box.get('status', '?')})  → PVUEB_WALLBOX_ID={box.get('id')}")
        print()

        charger_id = os.environ.get("PVUEB_WALLBOX_ID") or liste[0].get("id")
        while True:
            daten = await status(sitzung, token, charger_id)
            jetzt = datetime.datetime.now().strftime("%H:%M:%S")
            leistung = daten.get("charging_power")
            print(f"{jetzt}  Box {charger_id}: "
                  f"Ladeleistung {leistung} kW | "
                  f"Sitzung {daten.get('added_energy')} kWh | "
                  f"Status {daten.get('status_id')} | "
                  f"Auto verbunden: {daten.get('car_plugged', '?')}")
            if not args.watch:
                interessant = ("charging_power", "added_energy", "status_id",
                               "charging_speed", "max_available_power", "depot_price")
                print("\n  weitere Felder:")
                for schluessel in interessant:
                    if schluessel in daten:
                        print(f"    {schluessel:22s} {daten[schluessel]}")
                print(f"\n  (Antwort enthält {len(daten)} Felder insgesamt)")
                return
            await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
