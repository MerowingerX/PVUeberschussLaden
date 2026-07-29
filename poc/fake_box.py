#!/usr/bin/env python3
"""Wallbox-Attrappe auf Protokollebene — echter WebSocket, echte ocpp-Bibliothek.

Die Attrappen in test_sim.py und der obere Teil von test_robust.py hängen direkt
an `charge_loop.charge_point`. Damit ist die Regellogik geprüft, aber die Naht
zwischen unserem Code und dem Gerät gar nicht — und genau dort ist der Ausfall
vom 28.07.2026 entstanden (docs/issue_nightly_load_did_not_work.md). Diese
Attrappe spricht stattdessen OCPP 1.6J über eine echte WebSocket-Verbindung
gegen unseren eigenen Server.

Sie bildet nicht die Norm nach, sondern **diese eine Pulsar Plus**. Jede
Eigenheit trägt unten ihren Beweisstatus. Das ist keine Förmlichkeit: eine
vermutete Eigenheit als Test festzuschreiben erzeugt einen grünen Haken für
Verhalten, das nie jemand gesehen hat.

## Belegte Eigenheiten (aus Logs und Vorfallberichten)

| Schalter | Verhalten | Quelle |
|---|---|---|
| `haelt_limit=False` | quittiert SetChargingProfile mit Accepted, lädt trotzdem mit `eigen_a` | docs/issue_limit_to_6A.md |
| `profil_vor_transaktion_verfaellt` | ein vor der Transaktion gesetztes TxDefaultProfile wird beim Sessionstart verworfen | Logauszug 23.07.2026 |
| `start_akzeptiert_dann_abgelehnt` | erster RemoteStart Accepted, der nächste Rejected | Log 28.07.2026, 06:03:57/06:04:28 |
| `meldet_leistung=False` | keine Power.Active.Import in MeterValues | Grundlage der Schätzung aus dem Limit |
| `gesperrt` | App-/RFID-Sperre steht, blockiert RemoteStart aber nicht | Nacht 23.07.2026 (einmalige Beobachtung) |

## Offene Hypothesen — nur benannt, nicht als Wahrheit getestet

| Schalter | Vermutung | Status |
|---|---|---|
| `zeitfenster` | Box befolgt einen App-Zeitplan auch im OCPP-Betrieb | **ungeprüft**, siehe features/feature_WallboxRueckfallebene.md |
| `laedt_offline` | Box lädt autonom, wenn der OCPP-Server unerreichbar ist | **ungeprüft** |

Tests, die diese beiden benutzen, sagen „*wenn* die Box das tut, dann verhält
sich PVueb so" — sie behaupten nichts über die Box.
"""

import asyncio
import datetime

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call, call_result

VOLTAGE, PHASES = 230, 3


class FakeBox(OcppChargePoint):
    """Die Wallbox-Seite der OCPP-Verbindung.

    Anders als bei einer Objekt-Attrappe läuft hier alles durch Serialisierung,
    WebSocket und die echte Nachrichtenzuordnung der ocpp-Bibliothek.
    """

    def __init__(self, cp_id, websocket, *, haelt_limit=True, eigen_a=6.0,
                 profil_vor_transaktion_verfaellt=False,
                 start_akzeptiert_dann_abgelehnt=False,
                 meldet_leistung=True, gesperrt=False,
                 zeitfenster=None, auto_nimmt_a=None):
        super().__init__(cp_id, websocket)
        self.haelt_limit = haelt_limit
        self.eigen_a = eigen_a
        self.profil_vor_transaktion_verfaellt = profil_vor_transaktion_verfaellt
        self.start_akzeptiert_dann_abgelehnt = start_akzeptiert_dann_abgelehnt
        self.meldet_leistung = meldet_leistung
        self.gesperrt = gesperrt
        self.zeitfenster = zeitfenster        # (start_h, end_h) oder None
        self.auto_nimmt_a = auto_nimmt_a      # Obergrenze des Fahrzeugs, None = nimmt alles

        self.profil_a = None                  # was per OCPP gesetzt wurde
        self.transaktion = None
        self.laeuft = False
        self.starts = 0
        self.ereignisse: list[tuple[str, object]] = []
        self.konfiguration: dict[str, str] = {}

    # --- was wirklich fließt, unabhängig davon, was der Regler glaubt --------

    def echte_a(self) -> float:
        """Die Stromstärke, die das Fahrzeug tatsächlich bekommt."""
        if not self.laeuft:
            return 0.0
        erlaubt = self.profil_a if (self.haelt_limit and self.profil_a is not None) \
            else self.eigen_a
        if self.auto_nimmt_a is not None:
            erlaubt = min(erlaubt, self.auto_nimmt_a)
        return erlaubt

    def im_zeitfenster(self, jetzt: datetime.datetime | None = None) -> bool:
        """Hypothese `zeitfenster`: liegt die Uhrzeit im App-Zeitplan?"""
        if self.zeitfenster is None:
            return True
        start, ende = self.zeitfenster
        if start == ende:
            return False                      # leeres Fenster = dauerhaft zu
        stunde = (jetzt or datetime.datetime.now()).hour
        return start <= stunde < ende if start < ende else (stunde >= start or stunde < ende)

    # --- CSMS -> Box --------------------------------------------------------

    @on("SetChargingProfile")
    def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kwargs):
        # Die ocpp-Bibliothek reicht die Nutzlast in snake_case durch, auch
        # verschachtelt — nicht in der Schreibweise, die über die Leitung geht.
        plan = cs_charging_profiles["charging_schedule"]["charging_schedule_period"][0]
        grenze = float(plan["limit"])
        zweck = cs_charging_profiles.get("charging_profile_purpose")
        self.ereignisse.append(("SetChargingProfile", (grenze, zweck)))
        # Eigenheit: ein TxDefaultProfile, das vor der Transaktion eintrifft,
        # überlebt den Sessionstart nicht — die Box eröffnet mit ihrem Eigenwert.
        if (self.profil_vor_transaktion_verfaellt
                and zweck == "TxDefaultProfile" and self.transaktion is None):
            return call_result.SetChargingProfile(status="Accepted")   # quittiert, verworfen
        self.profil_a = grenze
        return call_result.SetChargingProfile(status="Accepted")

    @on("RemoteStartTransaction")
    def on_remote_start(self, id_tag, **kwargs):
        self.starts += 1
        self.ereignisse.append(("RemoteStart", self.starts))
        # Eigenheit: der zweite Versuch wird abgelehnt, während die Box noch
        # in "Preparing" hängt (Log 28.07., zwölfmal in anderthalb Stunden)
        if self.start_akzeptiert_dann_abgelehnt and self.starts > 1:
            return call_result.RemoteStartTransaction(status="Rejected")
        # Hypothese: außerhalb des App-Zeitplans nimmt die Box keinen Start an
        if not self.im_zeitfenster():
            return call_result.RemoteStartTransaction(status="Rejected")
        asyncio.get_event_loop().create_task(self._starten())
        return call_result.RemoteStartTransaction(status="Accepted")

    @on("RemoteStopTransaction")
    def on_remote_stop(self, transaction_id, **kwargs):
        self.ereignisse.append(("RemoteStop", transaction_id))
        if self.transaktion != transaction_id:
            return call_result.RemoteStopTransaction(status="Rejected")
        asyncio.get_event_loop().create_task(self._stoppen())
        return call_result.RemoteStopTransaction(status="Accepted")

    @on("ChangeConfiguration")
    def on_change_configuration(self, key, value, **kwargs):
        self.konfiguration[key] = value
        return call_result.ChangeConfiguration(status="Accepted")

    @on("ChangeAvailability")
    def on_change_availability(self, connector_id, type, **kwargs):
        self.ereignisse.append(("ChangeAvailability", type))
        return call_result.ChangeAvailability(status="Accepted")

    # --- Box -> CSMS --------------------------------------------------------

    async def anmelden(self):
        await self.call(call.BootNotification(
            charge_point_model="PLP1-0-2-3", charge_point_vendor="Wall Box Chargers"))
        await self.melde_status("Available")

    async def melde_status(self, status: str):
        await self.call(call.StatusNotification(
            connector_id=1, error_code="NoError", status=status,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()))

    async def herzschlag(self):
        await self.call(call.Heartbeat())

    async def messwerte(self):
        """MeterValues wie die echte Box — mit oder ohne Momentanleistung."""
        proben = [{"value": str(round(self.echte_a() * VOLTAGE * PHASES)),
                   "measurand": "Power.Active.Import", "unit": "W"}] \
            if self.meldet_leistung else []
        proben.append({"value": "7425880", "measurand": "Energy.Active.Import.Register",
                       "unit": "Wh"})
        await self.call(call.MeterValues(
            connector_id=1, transaction_id=self.transaktion,
            meter_value=[{"timestamp": datetime.datetime.now(
                datetime.timezone.utc).isoformat(), "sampled_value": proben}]))

    async def _starten(self):
        antwort = await self.call(call.StartTransaction(
            connector_id=1, id_tag="pvueb", meter_start=7425880,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        self.transaktion = antwort.transaction_id
        # Eigenheit: die Session eröffnet mit dem Eigenwert der Box, ein zuvor
        # gesetztes TxDefaultProfile ist damit weg
        if self.profil_vor_transaktion_verfaellt:
            self.profil_a = None
        self.laeuft = True
        await self.melde_status("Charging")

    async def _stoppen(self):
        self.laeuft = False
        await self.melde_status("Available")
        self.transaktion = None


async def verbinde(port: int, **eigenheiten) -> tuple[FakeBox, object, asyncio.Task]:
    """Attrappe an unseren OCPP-Server hängen. Rückgabe: (Box, Socket, Task)."""
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/1", subprotocols=["ocpp1.6"])
    box = FakeBox("1", ws, **eigenheiten)
    task = asyncio.create_task(box.start())
    return box, ws, task
