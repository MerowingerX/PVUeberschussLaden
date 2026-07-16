#!/usr/bin/env python3
"""M2-PoC: Minimaler OCPP-1.6J-Server (CSMS) für die Wallbox Pulsar Pro.

Aufruf:
    python ocpp_server.py [--port 9000]

Danach in der Wallbox-App unter OCPP die URL eintragen:
    ws://<ip-dieses-rechners>:9000/

Der Server loggt alle Nachrichten der Wallbox und nimmt Kommandos von stdin:
    limit <ampere>   Stromlimit setzen (SetChargingProfile, TxDefaultProfile)
    start            Ladevorgang starten (RemoteStartTransaction)
    stop             Ladevorgang stoppen (RemoteStopTransaction)
    trigger          MeterValues anfordern (TriggerMessage)
    quit             Server beenden
"""

import argparse
import asyncio
import datetime
import logging
import os
import sys

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    AuthorizationStatus,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingRateUnitType,
    MessageTrigger,
    RegistrationStatus,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("csms")


class ChargePoint(OcppChargePoint):
    transaction_id: int | None = None

    @on("BootNotification")
    def on_boot(self, charge_point_vendor, charge_point_model, **kwargs):
        log.info("BootNotification: %s %s %s", charge_point_vendor, charge_point_model, kwargs)
        return call_result.BootNotification(
            current_time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            interval=30,
            status=RegistrationStatus.accepted,
        )

    @on("Heartbeat")
    def on_heartbeat(self):
        return call_result.Heartbeat(
            current_time=datetime.datetime.now(datetime.timezone.utc).isoformat()
        )

    @on("StatusNotification")
    def on_status(self, connector_id, error_code, status, **kwargs):
        log.info("Status connector %s: %s (%s)", connector_id, status, error_code)
        return call_result.StatusNotification()

    @on("Authorize")
    def on_authorize(self, id_tag):
        log.info("Authorize: %s", id_tag)
        return call_result.Authorize(id_tag_info={"status": AuthorizationStatus.accepted})

    @on("StartTransaction")
    def on_start_transaction(self, connector_id, id_tag, meter_start, **kwargs):
        self.transaction_id = 1
        log.info("StartTransaction: connector %s, meter %s Wh", connector_id, meter_start)
        return call_result.StartTransaction(
            transaction_id=self.transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on("StopTransaction")
    def on_stop_transaction(self, meter_stop, transaction_id, **kwargs):
        self.transaction_id = None
        log.info("StopTransaction %s: meter %s Wh, %s", transaction_id, meter_stop, kwargs)
        return call_result.StopTransaction()

    @on("MeterValues")
    def on_meter_values(self, connector_id, meter_value, **kwargs):
        for entry in meter_value:
            for sample in entry.get("sampledValue", []):
                log.info(
                    "MeterValue: %s = %s %s",
                    sample.get("measurand", "Energy.Active.Import.Register"),
                    sample.get("value"),
                    sample.get("unit", ""),
                )
        return call_result.MeterValues()

    @on("DataTransfer")
    def on_data_transfer(self, vendor_id, **kwargs):
        log.info("DataTransfer von %s: %s", vendor_id, kwargs)
        return call_result.DataTransfer(status="Accepted")

    async def set_current_limit(self, amps: float):
        request = call.SetChargingProfile(
            connector_id=1,
            cs_charging_profiles={
                "charging_profile_id": 1,
                "stack_level": 0,
                "charging_profile_purpose": ChargingProfilePurposeType.tx_default_profile,
                "charging_profile_kind": ChargingProfileKindType.absolute,
                "charging_schedule": {
                    "charging_rate_unit": ChargingRateUnitType.amps,
                    "charging_schedule_period": [{"start_period": 0, "limit": amps}],
                },
            },
        )
        response = await self.call(request)
        log.info("SetChargingProfile(%s A): %s", amps, response.status)

    async def remote_start(self):
        response = await self.call(call.RemoteStartTransaction(id_tag="pvueb", connector_id=1))
        log.info("RemoteStartTransaction: %s", response.status)

    async def remote_stop(self):
        if self.transaction_id is None:
            log.warning("Keine laufende Transaktion bekannt")
            return
        response = await self.call(call.RemoteStopTransaction(transaction_id=self.transaction_id))
        log.info("RemoteStopTransaction: %s", response.status)

    async def trigger_meter_values(self):
        response = await self.call(
            call.TriggerMessage(requested_message=MessageTrigger.meter_values, connector_id=1)
        )
        log.info("TriggerMessage(MeterValues): %s", response.status)


charge_point: ChargePoint | None = None


async def on_connect(websocket):
    global charge_point
    path = websocket.request.path
    charge_point_id = path.strip("/") or "unknown"
    log.info("Wallbox verbunden: id=%r, subprotocol=%s", charge_point_id, websocket.subprotocol)
    charge_point = ChargePoint(charge_point_id, websocket)
    try:
        await charge_point.start()
    except websockets.exceptions.ConnectionClosed:
        log.warning("Wallbox-Verbindung getrennt")
    finally:
        charge_point = None


async def command_loop():
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            await asyncio.sleep(1)
            continue
        parts = line.strip().split()
        if not parts:
            continue
        cmd = parts[0].lower()
        if cmd == "quit":
            log.info("Beende Server")
            os._exit(0)
        if charge_point is None:
            log.warning("Noch keine Wallbox verbunden")
            continue
        try:
            if cmd == "limit" and len(parts) == 2:
                await charge_point.set_current_limit(float(parts[1]))
            elif cmd == "start":
                await charge_point.remote_start()
            elif cmd == "stop":
                await charge_point.remote_stop()
            elif cmd == "trigger":
                await charge_point.trigger_meter_values()
            else:
                print(__doc__)
        except Exception as exc:  # noqa: BLE001 - PoC: alles anzeigen
            log.error("Kommando fehlgeschlagen: %s", exc)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    server = await websockets.serve(
        on_connect, "0.0.0.0", args.port, subprotocols=["ocpp1.6"]
    )
    log.info("OCPP-Server läuft auf ws://0.0.0.0:%s/ — warte auf Wallbox ...", args.port)
    await asyncio.gather(server.wait_closed(), command_loop())


if __name__ == "__main__":
    asyncio.run(main())
