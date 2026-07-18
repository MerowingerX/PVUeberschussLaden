#!/usr/bin/env python3
"""M3-PoC: Regel-Loop — verbindet Sun2000-Messung mit Pulsar-Pro-Steuerung.

Aufruf:
    python charge_loop.py [--inverter <ip>] [--port 9000]

Wechselrichter-IP kommt aus --inverter, der Umgebungsvariable PVUEB_INVERTER_IP
oder einer .env-Datei im Projektverzeichnis (siehe .env.example).

Stdin-Kommandos:
    mode pv | minpv | fast   Lademodus wechseln (Start: pv)
    frei / sperr             Laden freigeben / Freigabe zurücknehmen (Start: gesperrt)
    status                   aktuelle Werte anzeigen
    quit                     beenden

Nachttarif: 00:00–08:00 lädt ein freigegebenes, eingestecktes Fahrzeug
unabhängig vom Modus mit voller Leistung (16 A). Ab 08:00 gilt wieder
der eingestellte Modus.

minpv-Trigger (PVUEB_MINPV_*): Start erst, wenn der echte PV-Überschuss
(inkl. Batterie-Korrektur: Hausbatterie-Ladung zählt als verfügbar,
Entladung nicht) über START_FACTOR × Min-Leistung liegt. Während der
Ladung überbrückt ein Timeout (TIMEOUT_MIN) Wolkenlöcher: unter
PAUSE_FACTOR × Min startet er, über RESUME_FACTOR × Min wird er
verworfen, bei Ablauf stoppt die Ladung.

Der Überschuss wird als Netzleistung + Ladeleistung gerechnet, die eigene
Ladung also wieder herausgerechnet. Die Ladeleistung kommt aus den
MeterValues der Box (Power.Active.Import, ersatzweise aus der Differenz des
Energiezählers); fehlt beides oder ist der Wert älter als
PVUEB_CHARGE_W_MAX_AGE_S, wird er aus dem gesetzten Limit geschätzt — sonst
sähe der Regler beim Ladestart einen PV-Einbruch, der keiner ist.

Ladeleistung folgt dem gleitenden Mittel des echten PV-Überschusses (inkl.
Batterie-Ladeleistung, die das Auto bekommen kann), gedeckelt auf den
Momentanwert plus Batterie-Boost. Zwei Fenster: PVUEB_AVG_WINDOW_MIN beim
Regeln der laufenden Ladung, PVUEB_START_AVG_MIN (kurz) für die
Startentscheidung, damit der Start nicht dem trägen Mittel hinterherhinkt.
Batterie-Boost (PVUEB_BOOST_*): bricht die PV-Leistung
kurz ein (Wolke), schiebt die Hausbatterie bis zu PVUEB_BOOST_W nach, statt die
Ladung herunterzuregeln — Tagesbudget PVUEB_BOOST_W × PVUEB_BOOST_H, nur bei
laufender Ladung und SOC über PVUEB_BOOST_MIN_SOC. Nachgeladen wird die
Batterie danach aus PV oder nachts über die Netzladung.

Batterie-Netzladung (LUNA2000-Zwangsladung, Register verifiziert 2026-07-16):
lädt mit PVUEB_BATT_CHARGE_W bis PVUEB_BATT_TARGET_SOC, der Wechselrichter
stoppt am Ziel selbst. Manuell jederzeit über den Web-Toggle; Automatik
startet höchstens einmal pro Nacht, wenn Nachtfenster aktiv, SOC unter
PVUEB_BATT_LOW_SOC und die Sonnenprognose (forecast.solar) unter
PVUEB_FORECAST_MIN_KWH liegt.
"""

import argparse
import asyncio
import collections
import datetime
import logging
import os
import sys

import aiohttp
import websockets
from aiohttp import web
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    AuthorizationStatus,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingRateUnitType,
    RegistrationStatus,
)
from pymodbus.client import AsyncModbusTcpClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("loop")
logging.getLogger("ocpp").setLevel(logging.WARNING)

# --- Anlagenkonstanten (bewusst fest verdrahtet, siehe PLAN.md Designprinzip) ---
PHASES = 3
VOLTAGE = 230
MIN_AMPS = 6
MAX_AMPS = 16
MIN_CHARGE_W = MIN_AMPS * PHASES * VOLTAGE  # ~4140 W

# Regelzeit-Parameter (Poll-Takt, Anpass-Intervall, Start-/Stopp-Verzögerung)
# über .env (PVUEB_POLL_INTERVAL_S usw.), Defaults in State

# Nachttarif-Fenster: Tarif-Parameter, konfigurierbar über .env
# (PVUEB_NIGHT_START / PVUEB_NIGHT_END im Format HH:MM), Default 00:00-08:00
# Batterie-Netzladung: Schwellen/Leistung über .env (PVUEB_BATT_*), Defaults in State

REG_GRID_POWER = 37113       # int32, W, positiv = Einspeisung (verifiziert 2026-07-15)
REG_BATTERY_SOC = 37760      # uint16, ×0,1 % (verifiziert 2026-07-15)
REG_BATTERY_POWER = 37001    # int32, W, positiv = Laden

# LUNA2000-Zwangsladung (verifiziert 2026-07-16 gegen huawei_solar 3.0.6)
REG_FORCIBLE_CMD = 47100         # uint16: 0 = Stop, 1 = Laden, 2 = Entladen
REG_FORCIBLE_TARGET_SOC = 47101  # uint16, ×0,1 %
REG_FORCIBLE_MODE = 47246        # uint16: 0 = Dauer, 1 = Ziel-SOC
REG_FORCIBLE_CHARGE_W = 47247    # uint32, W

# Sonnenprognose (forecast.solar, kostenlos ohne Key; Anlagendaten aus .env)
FORECAST_URL = "https://api.forecast.solar/estimate/{lat}/{lon}/{tilt}/{azimut}/{kwp}"
FORECAST_REFRESH_S = 6 * 3600    # Abruf-Rhythmus (Limit wäre 12/h — wir sind weit drunter)
FORECAST_RETRY_S = 15 * 60       # Wartezeit nach Fehlversuch


class State:
    mode = "pv"                  # pv | minpv | fast
    released = False             # manuelle Freigabe (Knopfdruck)
    min_amps = MIN_AMPS          # Untergrenze im Modus minpv (justierbar im UI)
    night_enabled = True         # Nachtladen-Automatik an/aus
    heartbeat_s = 10             # OCPP-Heartbeat der Box (justierbar im UI, Default aus .env)
    night_start_min = 0          # Nachttarif-Beginn in Minuten seit 00:00 (aus .env)
    night_end_min = 8 * 60       # Nachttarif-Ende (aus .env)
    poll_interval_s = 5          # Modbus-Polling und Regel-Takt (aus .env)
    adjust_min_interval_s = 25   # Limit höchstens so oft ändern (aus .env)
    start_delay_s = 120          # Überschuss muss so lange reichen, bevor Start (aus .env)
    stop_delay_s = 180           # Überschuss muss so lange fehlen, bevor Stopp (aus .env)
    minpv_start_factor = 1.10    # minpv: Start erst ab Faktor × Min-Leistung PV-Überschuss (aus .env)
    minpv_pause_factor = 0.75    # minpv: darunter beginnt der Wolkenloch-Timeout (aus .env)
    minpv_resume_factor = 0.90   # minpv: darüber wird der Timeout wieder verworfen (aus .env)
    minpv_timeout_s = 600        # minpv: so lange werden Wolken überbrückt (aus .env)
    avg_window_s = 600           # Mittelungsfenster beim Regeln, 0 = aus (aus .env)
    start_avg_window_s = 120     # kürzeres Fenster für die Startentscheidung (aus .env)
    avg_active_s = 0             # gerade wirksames Fenster, nur fürs UI
    pv_hist: collections.deque = collections.deque()  # (Loop-Zeit, PV-Überschuss W)
    pv_avg_w: float | None = None           # geglätteter PV-Überschuss, nur fürs UI
    boost_w = 2500               # Batterie darf so viel ins Auto nachschieben (aus .env)
    boost_wh = 5000              # Tagesbudget dafür, 0 = Boost aus (aus .env)
    boost_min_soc = 30.0         # darunter kein Boost mehr (aus .env)
    boost_used_wh = 0.0          # heute schon verbrauchtes Boost-Budget
    boost_day: datetime.date | None = None  # Tag, für den boost_used_wh gilt
    boost_last_t: float | None = None       # Loop-Zeit des letzten Boost-Takts
    batt_low_soc = 30.0          # Automatik-Startschwelle in % (aus .env)
    batt_target_soc = 80.0       # Netzladung bis zu diesem SOC (aus .env)
    batt_charge_w = 500          # Leistung der Zwangsladung in W (aus .env)
    lat = 52.25                  # Standort/Anlage für forecast.solar (aus .env)
    lon = 10.66
    pv_tilt = 42
    pv_azimut = 0                # forecast.solar-Konvention: 0 = Süd
    pv_kwp = 7.0
    forecast_min_kwh = 5.0       # „keine Sonne“ = Prognose unter diesem Wert (aus .env)
    forecast: dict[str, float] = {}      # Datum (ISO) -> prognostizierte kWh
    forecast_at: float | None = None     # Unix-Zeit des letzten erfolgreichen Abrufs
    grid_w: float | None = None  # positiv = Einspeisung
    soc: float | None = None     # Batterie-SOC in %
    battery_w: float | None = None  # Batterie-Leistung, positiv = Laden
    charge_w = 0.0               # letzte bekannte Ladeleistung
    charge_w_seen: float | None = None   # Loop-Zeit der letzten Messung dazu
    charge_w_src = "keine"       # gemessen | Energie | geschätzt | keine (fürs UI)
    charge_w_max_age_s = 30      # ältere Messung gilt als tot (aus .env)
    charge_energy_wh: float | None = None   # letzter Zählerstand der Box
    charge_energy_t: float | None = None    # Loop-Zeit dazu
    charging = False
    current_limit = 0.0          # zuletzt gesetztes Limit in A (Raster limit_step_a)
    limit_step_a = 0.1           # Auflösung des Ladelimits in A (aus .env)
    limit_deadband_a = 0.3       # so viel Abweichung, bevor neu gesetzt wird (aus .env)
    surplus_since: float | None = None   # Zeitstempel: Überschuss reicht seit ...
    deficit_since: float | None = None   # Zeitstempel: Überschuss fehlt seit ...
    minpv_low_since: float | None = None # Zeitstempel: PV-Überschuss unter Pause-Schwelle seit ...
    last_adjust = 0.0
    battery_grid_charge = False  # von uns gestartete Zwangsladung aktiv
    forcible_cmd: int | None = None      # gelesenes Register 47100 (0/1/2)
    battery_charge_auto = False  # aktive Zwangsladung kam von der Nachtautomatik
    battery_auto_started = False # Automatik hat diese Nacht schon gestartet (einmal pro Nacht)
    last_box_seen: float | None = None     # Unix-Zeit: letzter OCPP-Heartbeat
    last_huawei_seen: float | None = None  # Unix-Zeit: letzter erfolgreicher Modbus-Poll
    box_status = "unbekannt"               # letzter StatusNotification-Status der Box


def in_night_window(now: datetime.datetime | None = None) -> bool:
    if not state.night_enabled:
        return False
    t = now or datetime.datetime.now()
    minutes = t.hour * 60 + t.minute
    start, end = state.night_start_min, state.night_end_min
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end  # Fenster über Mitternacht (z. B. 23:00–08:00)


def reset_charge_meter():
    """Zählerstände der Wallbox verwerfen — nach Ladeende sind sie wertlos."""
    state.charge_w = 0.0
    state.charge_w_seen = None
    state.charge_w_src = "keine"
    state.charge_energy_wh = state.charge_energy_t = None


def charge_power(now: float) -> float:
    """Ladeleistung fürs Regeln, notfalls aus dem gesetzten Limit geschätzt.

    Der Überschuss wird als grid_w + charge_w gerechnet — die eigene Ladung
    muss also wieder herausgerechnet werden. Meldet die Box keine
    Momentanleistung (kein Power.Active.Import, kein Energiezähler) oder ist
    die letzte Messung tot, hielte der Regler sonst die eigene Ladung für
    einen PV-Einbruch, stoppte über den minpv-Timeout und startete sofort neu.
    Das Limit ist eine Obergrenze: zieht das Auto weniger, wird der Überschuss
    überschätzt und es fließt kurz Netzstrom — besser als der Stopp-Kreisel.
    """
    if not state.charging:
        return 0.0
    if (state.charge_w_seen is not None
            and now - state.charge_w_seen <= state.charge_w_max_age_s):
        return state.charge_w
    est = state.current_limit * VOLTAGE * PHASES
    if state.charge_w_src != "geschätzt":
        log.warning("Wallbox meldet keine Ladeleistung — schätze %.0f W aus dem "
                    "Limit %.1f A", est, state.current_limit)
        state.charge_w_src = "geschätzt"
    state.charge_w = est
    return est


state = State()
charge_point: "ChargePoint | None" = None
modbus_client: AsyncModbusTcpClient | None = None
modbus_lock = asyncio.Lock()  # serialisiert Polling und Schreibzugriffe (SDongle: 1 Verbindung)


class ChargePoint(OcppChargePoint):
    transaction_id: int | None = None

    @on("BootNotification")
    def on_boot(self, charge_point_vendor, charge_point_model, **kwargs):
        log.info("Wallbox gebootet: %s %s", charge_point_vendor, charge_point_model)
        asyncio.get_event_loop().create_task(self.configure_metering())
        return call_result.BootNotification(
            current_time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            interval=state.heartbeat_s,
            status=RegistrationStatus.accepted,
        )

    async def change_configuration(self, key: str, value: str):
        try:
            response = await self.call(call.ChangeConfiguration(key=key, value=value))
            log.info("ChangeConfiguration %s=%s: %s", key, value,
                     response.status if response else "keine Antwort")
        except Exception as exc:  # noqa: BLE001
            log.warning("ChangeConfiguration %s fehlgeschlagen: %s", key, exc)

    async def configure_metering(self):
        await asyncio.sleep(2)  # Boot-Antwort erst rausgehen lassen
        await self.change_configuration("HeartbeatInterval", str(state.heartbeat_s))
        await self.change_configuration("MeterValueSampleInterval", "10")
        await self.change_configuration(
            "MeterValuesSampledData", "Power.Active.Import,Energy.Active.Import.Register"
        )

    @on("Heartbeat")
    def on_heartbeat(self):
        state.last_box_seen = datetime.datetime.now().timestamp()
        return call_result.Heartbeat(
            current_time=datetime.datetime.now(datetime.timezone.utc).isoformat()
        )

    @on("StatusNotification")
    def on_status(self, connector_id, error_code, status, **kwargs):
        log.info("Wallbox-Status: %s (%s)", status, error_code)
        state.box_status = status
        if status in ("Charging",):
            state.charging = True
        elif status in ("Available", "Finishing", "SuspendedEVSE", "SuspendedEV", "Faulted"):
            state.charging = False
            reset_charge_meter()
        return call_result.StatusNotification()

    @on("Authorize")
    def on_authorize(self, id_tag):
        return call_result.Authorize(id_tag_info={"status": AuthorizationStatus.accepted})

    @on("StartTransaction")
    def on_start_transaction(self, connector_id, id_tag, meter_start, **kwargs):
        self.transaction_id = 1
        state.charging = True
        log.info("Transaktion gestartet (meter %s Wh)", meter_start)
        return call_result.StartTransaction(
            transaction_id=self.transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on("StopTransaction")
    def on_stop_transaction(self, meter_stop, transaction_id, **kwargs):
        self.transaction_id = None
        state.charging = False
        reset_charge_meter()
        log.info("Transaktion beendet (meter %s Wh)", meter_stop)
        return call_result.StopTransaction()

    @on("MeterValues")
    def on_meter_values(self, connector_id, meter_value, **kwargs):
        state.last_box_seen = datetime.datetime.now().timestamp()
        now = asyncio.get_event_loop().time()
        power_wh = energy_wh = None
        for entry in meter_value:
            for sample in entry.get("sampledValue", []):
                # Ohne measurand meint OCPP 1.6 den Energiezähler
                measurand = sample.get("measurand", "Energy.Active.Import.Register")
                unit = (sample.get("unit") or "").lower()
                try:
                    value = float(sample["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                if measurand == "Power.Active.Import":
                    power_wh = value * 1000 if unit == "kw" else value
                elif measurand == "Energy.Active.Import.Register":
                    energy_wh = value * 1000 if unit == "kwh" else value
        if power_wh is not None:
            state.charge_w, state.charge_w_seen = power_wh, now
            state.charge_w_src = "gemessen"
        if energy_wh is not None:
            # Zähler mitschreiben: meldet die Box keine Momentanleistung,
            # liefert die Differenz pro Zeit denselben Wert
            if (power_wh is None and state.charge_energy_wh is not None
                    and state.charge_energy_t is not None
                    and now - state.charge_energy_t > 1
                    and energy_wh >= state.charge_energy_wh):
                delta_s = now - state.charge_energy_t
                state.charge_w = (energy_wh - state.charge_energy_wh) * 3600 / delta_s
                state.charge_w_seen = now
                state.charge_w_src = "Energie"
            state.charge_energy_wh, state.charge_energy_t = energy_wh, now
        return call_result.MeterValues()

    @on("DataTransfer")
    def on_data_transfer(self, vendor_id, **kwargs):
        return call_result.DataTransfer(status="Accepted")

    async def set_limit(self, amps: float):
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
        if response and response.status == "Accepted":
            state.current_limit = amps
            log.info("Limit gesetzt: %.1f A", amps)
        else:
            log.warning("Limit %.1f A abgelehnt: %s", amps, response)

    async def remote_start(self):
        response = await self.call(call.RemoteStartTransaction(id_tag="pvueb", connector_id=1))
        log.info("RemoteStart: %s", response.status if response else "keine Antwort")

    async def remote_stop(self):
        if self.transaction_id is None:
            return
        response = await self.call(call.RemoteStopTransaction(transaction_id=self.transaction_id))
        log.info("RemoteStop: %s", response.status if response else "keine Antwort")


async def on_connect(websocket):
    global charge_point
    charge_point_id = websocket.request.path.strip("/") or "unknown"
    log.info("Wallbox verbunden: %r", charge_point_id)
    charge_point = ChargePoint(charge_point_id, websocket)
    try:
        await charge_point.start()
    except websockets.exceptions.ConnectionClosed:
        log.warning("Wallbox-Verbindung getrennt")
    finally:
        charge_point = None


def decode_i32(words: list[int]) -> float:
    raw = (words[0] << 16) | words[1]
    if raw >= 1 << 31:
        raw -= 1 << 32
    return float(raw)


async def modbus_task(host: str):
    global modbus_client
    while True:
        # SDongle ist träge: langer Timeout, sonst Transaction-ID-Salat bei später Antwort
        client = AsyncModbusTcpClient(host, port=502, timeout=10)
        await client.connect()
        if client.connected:
            await asyncio.sleep(3)  # SDongle-Eigenheit nach Connect
            modbus_client = client
            while client.connected:
                try:
                    async with modbus_lock:
                        result = await client.read_holding_registers(
                            REG_GRID_POWER, count=2, device_id=1
                        )
                        if not result.isError():
                            state.grid_w = decode_i32(result.registers)
                            state.last_huawei_seen = datetime.datetime.now().timestamp()
                        await asyncio.sleep(0.3)  # SDongle nicht hetzen
                        soc_result = await client.read_holding_registers(
                            REG_BATTERY_SOC, count=1, device_id=1
                        )
                        if not soc_result.isError():
                            state.soc = soc_result.registers[0] * 0.1
                        await asyncio.sleep(0.3)
                        batt_result = await client.read_holding_registers(
                            REG_BATTERY_POWER, count=2, device_id=1
                        )
                        if not batt_result.isError():
                            state.battery_w = decode_i32(batt_result.registers)
                        await asyncio.sleep(0.3)
                        cmd_result = await client.read_holding_registers(
                            REG_FORCIBLE_CMD, count=1, device_id=1
                        )
                        if not cmd_result.isError():
                            state.forcible_cmd = cmd_result.registers[0]
                            if state.forcible_cmd == 0 and state.battery_grid_charge:
                                state.battery_grid_charge = False
                                state.battery_charge_auto = False
                                log.info("Batterie-Netzladung vom Wechselrichter beendet "
                                         "(Ziel-SOC erreicht oder extern gestoppt)")
                except Exception as exc:  # noqa: BLE001
                    log.warning("Modbus-Fehler: %s", exc)
                    break
                await asyncio.sleep(state.poll_interval_s)
        modbus_client = None
        client.close()
        state.grid_w = None
        state.battery_w = None
        log.warning("Modbus getrennt, Reconnect in 10 s")
        await asyncio.sleep(10)


def pv_average(now: float, pv_surplus: float) -> float:
    """Gleitender Mittelwert des PV-Überschusses.

    Zwei Fenster: beim Regeln der laufenden Ladung das träge
    PVUEB_AVG_WINDOW_MIN, damit einzelne Wolken das Limit nicht im
    Sekundentakt treiben — für die Startentscheidung das kurze
    PVUEB_START_AVG_MIN, damit die Ladung nicht dem Mittel hinterherhinkt.
    Fenster 0 schaltet die jeweilige Glättung ab.
    """
    window = state.avg_window_s if state.charging else state.start_avg_window_s
    state.avg_active_s = window
    state.pv_hist.append((now, pv_surplus))
    if window <= 0:
        state.pv_avg_w = pv_surplus
    else:
        recent = [w for t, w in state.pv_hist if t >= now - window]
        state.pv_avg_w = sum(recent) / len(recent) if recent else pv_surplus
    # Historie nur so weit vorhalten, wie das größere Fenster sie braucht
    keep = now - max(state.avg_window_s, state.start_avg_window_s)
    while state.pv_hist and state.pv_hist[0][0] < keep:
        state.pv_hist.popleft()
    return state.pv_avg_w


def boost_allowance(now: float) -> float:
    """Wieviel Batterie-Entladung darf gerade ins Auto fließen (W).

    Tagesbudget PVUEB_BOOST_WH, Leistung gedeckelt auf PVUEB_BOOST_W, Stopp
    unter PVUEB_BOOST_MIN_SOC. Verbraucht wird nur, was die Batterie wirklich
    entlädt, während das Auto lädt — Hausverbrauch aus der Batterie zählt
    nicht. Budget-Reset um Mitternacht: tagsüber lädt die Batterie aus PV
    nach, notfalls nachts über die Netzladung.
    """
    today = datetime.date.today()
    if state.boost_day != today:
        state.boost_day = today
        state.boost_used_wh = 0.0
    if state.boost_wh <= 0 or state.soc is None or state.soc < state.boost_min_soc:
        state.boost_last_t = None
        return 0.0
    rest_wh = state.boost_wh - state.boost_used_wh
    if rest_wh <= 0:
        state.boost_last_t = None
        return 0.0
    if state.charging and state.battery_w is not None and state.battery_w < 0:
        drawn = min(-state.battery_w, state.boost_w)
        if state.boost_last_t is not None:
            state.boost_used_wh += drawn * (now - state.boost_last_t) / 3600
    state.boost_last_t = now if state.charging else None
    # Restbudget für weniger als eine Minute Vollleistung nicht mehr anbieten
    return state.boost_w if rest_wh * 60 >= state.boost_w else 0.0


def target_amps(surplus_w: float) -> float:
    """Sollstrom auf dem Raster PVUEB_LIMIT_STEP_A, immer abgerundet.

    OCPP überträgt das Limit als Dezimalwert, die Box setzt also auch
    Zwischenwerte um. Feines Raster heißt: der Rest, der beim Abrunden auf
    ganze Ampere liegen bliebe (bis zu 690 W), geht ins Auto statt in die
    Einspeisung. Abgerundet wird trotzdem, damit der Sollwert nie über dem
    Überschuss liegt und Netzstrom zieht.
    """
    step = state.limit_step_a
    amps = int(surplus_w / (VOLTAGE * PHASES) / step) * step
    return round(max(0.0, min(float(MAX_AMPS), amps)), 3)


async def start_battery_grid_charge() -> str | None:
    """Startet die LUNA2000-Zwangsladung bis zum Ziel-SOC.

    Schreibreihenfolge wie huawei_solar-Service forcible_charge_soc:
    Leistung → Ziel-SOC → Modus(SOC) → Befehl(Laden). Ziel-SOC-Modus als
    Sicherheitsnetz: der Wechselrichter stoppt am Ziel selbst, auch wenn
    dieses Skript abstürzt. Gibt None bei Erfolg zurück, sonst Fehlertext.
    """
    client = modbus_client  # Snapshot gegen Reconnect-Race
    if client is None or not client.connected:
        return "Keine Modbus-Verbindung zum Wechselrichter"
    charge_w = int(state.batt_charge_w)
    sequence = [
        (REG_FORCIBLE_CHARGE_W, [charge_w >> 16, charge_w & 0xFFFF]),
        (REG_FORCIBLE_TARGET_SOC, [int(state.batt_target_soc * 10)]),
        (REG_FORCIBLE_MODE, [1]),
        (REG_FORCIBLE_CMD, [1]),
    ]
    try:
        async with modbus_lock:
            for register, words in sequence:
                result = await client.write_registers(register, words, device_id=1)
                if result.isError():
                    log.warning("Batterie-Netzladung: Schreibfehler Register %s: %s", register, result)
                    return f"Schreibfehler Register {register}: {result}"
                await asyncio.sleep(0.3)  # SDongle nicht hetzen
    except Exception as exc:  # noqa: BLE001
        log.warning("Batterie-Netzladung: %s", exc)
        return str(exc)
    state.battery_grid_charge = True
    log.info("Batterie-Netzladung gestartet: %d W bis %.0f %% (SOC jetzt %s %%)",
             charge_w, state.batt_target_soc, state.soc)
    return None


async def stop_battery_grid_charge() -> str | None:
    """Stoppt die Zwangsladung (Befehl = Stop). None bei Erfolg, sonst Fehlertext."""
    client = modbus_client  # Snapshot gegen Reconnect-Race
    if client is None or not client.connected:
        return "Keine Modbus-Verbindung zum Wechselrichter"
    try:
        async with modbus_lock:
            result = await client.write_registers(REG_FORCIBLE_CMD, [0], device_id=1)
            if result.isError():
                log.warning("Batterie-Netzladung: Stop fehlgeschlagen: %s", result)
                return f"Stop fehlgeschlagen: {result}"
    except Exception as exc:  # noqa: BLE001
        log.warning("Batterie-Netzladung: %s", exc)
        return str(exc)
    state.battery_grid_charge = False
    state.battery_charge_auto = False
    log.info("Batterie-Netzladung gestoppt (SOC %s %%)", state.soc)
    return None


def relevant_forecast() -> tuple[str, float | None]:
    """(Label, kWh) der nächsten Tageslichtperiode.

    Nachts nach Mitternacht zählt die Sonne von heute, abends die von morgen —
    Grenze pragmatisch 12:00. Fürs Nachtfenster (egal ob 00–08 oder 22–06 Uhr)
    ergibt das immer den kommenden Tag mit Sonne.
    """
    now = datetime.datetime.now()
    if now.hour < 12:
        return "heute", state.forecast.get(now.date().isoformat())
    tomorrow = now.date() + datetime.timedelta(days=1)
    return "morgen", state.forecast.get(tomorrow.isoformat())


async def forecast_task():
    """Holt die PV-Prognose regelmäßig von forecast.solar (alle 6 h, Retry 15 min)."""
    url = FORECAST_URL.format(lat=state.lat, lon=state.lon, tilt=state.pv_tilt,
                              azimut=state.pv_azimut, kwp=state.pv_kwp)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            state.forecast = {day: wh / 1000
                              for day, wh in data["result"]["watt_hours_day"].items()}
            state.forecast_at = datetime.datetime.now().timestamp()
            log.info("Prognose: %s", "  ".join(f"{d}: {kwh:.1f} kWh"
                                               for d, kwh in sorted(state.forecast.items())))
        except Exception as exc:  # noqa: BLE001
            log.warning("Prognose-Abruf fehlgeschlagen (%s) — neuer Versuch in %d min",
                        exc, FORECAST_RETRY_S // 60)
            await asyncio.sleep(FORECAST_RETRY_S)
            continue
        await asyncio.sleep(FORECAST_REFRESH_S)


async def battery_night_check():
    """Nachtautomatik: Batterie aus dem Netz laden, wenn alles zusammenkommt.

    Start (höchstens einmal pro Nacht): Nachtfenster aktiv + SOC unter
    PVUEB_BATT_LOW_SOC + Prognose der nächsten Tageslichtperiode unter
    PVUEB_FORECAST_MIN_KWH. Ohne Prognose (API-Fehler) wird nicht geladen.
    Am Ziel-SOC stoppt der Wechselrichter selbst; am Fensterende stoppen wir
    eine automatisch gestartete Ladung. Der Web-Toggle bleibt unabhängig
    nutzbar und wird von der Automatik nicht angefasst.
    """
    if not in_night_window():
        state.battery_auto_started = False  # nächste Nacht darf wieder starten
        if state.battery_grid_charge and state.battery_charge_auto:
            log.info("NACHT: Fensterende — stoppe automatische Netzladung (SOC %s %%)", state.soc)
            await stop_battery_grid_charge()
        return
    if (state.soc is None or state.battery_grid_charge or state.battery_auto_started
            or state.soc >= state.batt_low_soc):
        return
    label, kwh = relevant_forecast()
    if kwh is None or kwh >= state.forecast_min_kwh:
        return
    log.info("NACHT: SOC %.0f %% < %.0f %% und Prognose %s nur %.1f kWh < %.1f kWh — "
             "starte Netzladung auf %.0f %%", state.soc, state.batt_low_soc, label, kwh,
             state.forecast_min_kwh, state.batt_target_soc)
    error = await start_battery_grid_charge()
    if error is None:
        state.battery_auto_started = True
        state.battery_charge_auto = True
    else:
        log.warning("NACHT: Start fehlgeschlagen (%s) — neuer Versuch im nächsten Takt", error)


async def control_task():
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(state.poll_interval_s)
        await battery_night_check()
        if charge_point is None or state.grid_w is None:
            continue
        await control_step(loop.time())


async def control_step(now: float):
    """Ein Regeltakt. Getrennt von control_task, damit test_sim.py ihn mit
    simulierter Zeit und simulierter Wallbox durchspielen kann."""
    if not state.released:
        if state.charging:
            log.info("Freigabe fehlt — stoppe Ladung")
            await charge_point.remote_stop()
        state.surplus_since = state.deficit_since = state.minpv_low_since = None
        return

    # Nachttarif-Fenster oder Modus "fast": volle Leistung
    if state.mode == "fast" or in_night_window():
        if state.current_limit != MAX_AMPS:
            await charge_point.set_limit(MAX_AMPS)
        if not state.charging:
            await charge_point.remote_start()
        return

    surplus = state.grid_w + charge_power(now)
    # Echter PV-Überschuss: Batterie-Ladung wäre für das Auto verfügbar,
    # Batterie-Entladung täuscht Überschuss nur vor (Netzpunkt bleibt ~0)
    pv_surplus = surplus + (state.battery_w or 0)
    pv_avg = pv_average(now, pv_surplus)
    # Boost erst ab laufender Ladung: die Batterie hält eine Ladung durch
    # Wolken, startet sie aber nicht
    boost = boost_allowance(now) if state.charging else 0.0
    # Ziel ist der Mittelwert, gedeckelt auf das, was gerade wirklich da
    # ist (PV + Batterie-Boost) — sonst zöge die Lücke Strom aus dem Netz
    avail = min(pv_avg, pv_surplus + boost)
    amps = target_amps(avail)
    min_w = state.min_amps * VOLTAGE * PHASES
    if state.mode == "minpv":
        amps = max(state.min_amps, amps)

    if not state.charging:
        if state.mode == "minpv":
            ready = avail >= state.minpv_start_factor * min_w
        else:
            ready = amps >= MIN_AMPS
        if ready:
            state.deficit_since = None
            state.surplus_since = state.surplus_since or now
            if now - state.surplus_since >= state.start_delay_s:
                log.info("Überschuss %.0f W seit %ds — starte Ladung mit %.1f A",
                         surplus, state.start_delay_s, amps)
                await charge_point.set_limit(amps)
                await charge_point.remote_start()
                state.last_adjust = now
        else:
            state.surplus_since = None
    else:
        if state.mode == "minpv":
            # Wolkenloch-Überbrückung: unter Pause-Schwelle läuft ein Timeout,
            # Erholung über Resume-Schwelle verwirft ihn, Ablauf stoppt die Ladung
            if avail > state.minpv_resume_factor * min_w:
                if state.minpv_low_since is not None:
                    log.info("minpv: Überschuss erholt (%.0f W, davon %.0f W Batterie-Boost) "
                             "— Wolkenloch überbrückt", avail, boost)
                state.minpv_low_since = None
            elif avail < state.minpv_pause_factor * min_w:
                if state.minpv_low_since is None:
                    state.minpv_low_since = now
                    log.info("minpv: Überschuss nur %.0f W (< %.0f W) — stoppe in %ds, "
                             "falls keine Erholung", avail,
                             state.minpv_pause_factor * min_w, state.minpv_timeout_s)
            if (state.minpv_low_since is not None
                    and now - state.minpv_low_since >= state.minpv_timeout_s):
                log.info("minpv: PV-Überschuss seit %ds unter %.0f %% Min — stoppe Ladung",
                         state.minpv_timeout_s, state.minpv_resume_factor * 100)
                await charge_point.remote_stop()
                state.minpv_low_since = None
                state.surplus_since = state.deficit_since = None
                return
        if amps < MIN_AMPS:
            state.surplus_since = None
            state.deficit_since = state.deficit_since or now
            if now - state.deficit_since >= state.stop_delay_s:
                log.info("Überschuss weg (%.0f W) seit %ds — stoppe Ladung",
                         surplus, state.stop_delay_s)
                await charge_point.remote_stop()
        else:
            state.deficit_since = None
            if (abs(amps - state.current_limit) >= state.limit_deadband_a
                    and now - state.last_adjust >= state.adjust_min_interval_s):
                log.info("Überschuss %.0f W — passe Limit an: %.1f → %.1f A",
                         surplus, state.current_limit, amps)
                await charge_point.set_limit(amps)
                state.last_adjust = now


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
            os._exit(0)
        elif cmd == "mode" and len(parts) == 2 and parts[1] in ("pv", "minpv", "fast"):
            state.mode = parts[1]
            state.surplus_since = state.deficit_since = state.minpv_low_since = None
            log.info("Modus: %s", state.mode)
        elif cmd in ("frei", "release"):
            state.released = True
            log.info("Laden freigegeben")
        elif cmd in ("sperr", "lock"):
            state.released = False
            log.info("Freigabe zurückgenommen")
        elif cmd == "status":
            print(status_line())
        else:
            print(__doc__)


def status_line() -> str:
    surplus = (state.grid_w or 0) + state.charge_w
    return (f"Modus {state.mode} | Freigabe: {'ja' if state.released else 'NEIN'} "
            f"| Netz {state.grid_w} W | SOC {state.soc} % | Ladung {state.charge_w} W "
            f"| Überschuss {surplus:.0f} W | Limit {state.current_limit:.1f} A "
            f"| lädt: {state.charging} | Nachtfenster: {'ja' if in_night_window() else 'nein'} "
            f"| Batterie-Netzladung: {'ja' if state.battery_grid_charge else 'nein'} "
            f"| Box: {'ja' if charge_point else 'nein'}")


INDEX_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PVueb</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 28rem; margin: 1rem auto; padding: 0 1rem;
         background: #111; color: #eee; }
  h1 { font-size: 1.3rem; } .row { display:flex; justify-content:space-between; padding:.35rem 0;
  border-bottom:1px solid #333; } .val { font-variant-numeric: tabular-nums; }
  button { font-size:1.05rem; padding:.8rem 1rem; margin:.4rem 0 0 0; border-radius:.5rem;
           border:none; cursor:pointer; background:#333; color:#eee; display:block; width:100%;
           text-align:left; }
  button.on { background:#2e7d32; } button.warn { background:#c62828; }
  #frei { font-size:1.3rem; text-align:center; }
  input[type=range] { width:100%; accent-color:#2e7d32; }
  h2 { font-size:1rem; margin:1.2rem 0 .2rem; color:#aaa; }
  .slider-row { padding:.2rem .5rem .6rem; }
  .pages { display:flex; overflow-x:auto; scroll-snap-type:x mandatory;
           scrollbar-width:none; }
  .pages::-webkit-scrollbar { display:none; }
  .page { flex:0 0 100%; scroll-snap-align:start; scroll-snap-stop:always;
          box-sizing:border-box; padding:0 .1rem; }
  .dots { text-align:center; margin:.3rem 0; }
  .dot { display:inline-block; width:.5rem; height:.5rem; border-radius:50%;
         background:#555; margin:0 .3rem; }
  .dot.on { background:#eee; }
  .heart { display:inline-block; animation:pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { transform:scale(1); } 50% { transform:scale(1.3); } }
  .heart.dead { animation:none; filter:grayscale(1) brightness(.6); }
  .modes { border:1px solid #444; border-radius:.6rem; padding:.2rem .5rem .5rem; }
  button.mode { padding-left:2.4rem; position:relative; }
  button.mode::before { content:"○"; position:absolute; left:.9rem; }
  button.mode.on::before { content:"●"; }
</style></head><body>
<h1>PVueb – Überschussladen</h1>
<div class="dots"><span class="dot on"></span><span class="dot"></span><span class="dot"></span></div>
<div class="pages" id="pages">
<section class="page">
<div id="stat"></div>
<h2>Lademodus – genau eine Option</h2>
<div class="modes">
<button id="m_pv" class="mode" onclick="setMode('pv')">Reines PV-Überschussladen</button>
<button id="m_minpv" class="mode" onclick="setMode('minpv')">PV-Überschussladen, mindestens
  <span id="minlabel">6</span> A</button>
<button id="m_fast" class="mode" onclick="setMode('fast')">Sofort laden, mit voller Leistung</button>
</div>
<h2>Automatik</h2>
<button id="night" onclick="toggleNight()">…</button>
</section>
<section class="page">
<h2>Huawei-Batterie <span id="hheart"></span></h2>
<div id="batstat"></div>
<button id="batt" onclick="toggleBatt()">…</button>
</section>
<section class="page">
<h2>Debug</h2>
<button id="frei" onclick="toggleRelease()">…</button>
<div id="dbgstat"></div>
<h2>Mindest-Ladestrom: <span id="minlabel2">6</span> A</h2>
<div class="slider-row">
  <input type="range" id="minamps" min="6" max="16" step="1"
         oninput="document.getElementById('minlabel').textContent=this.value;
                  document.getElementById('minlabel2').textContent=this.value"
         onchange="setConfig({min_amps: +this.value})">
</div>
<h2>Box-Heartbeat: <span id="hblabel">10</span> s</h2>
<div class="slider-row">
  <input type="range" id="heartbeat" min="5" max="120" step="5"
         oninput="document.getElementById('hblabel').textContent=this.value"
         onchange="setConfig({heartbeat_s: +this.value})">
</div>
</section>
</div>
<script>
let s = {};
const WPA = 690;  // W pro Ampere: 3 Phasen × 230 V
function ago(sec) {
  if (sec === null || sec === undefined) return "noch nie";
  if (sec < 90) return "vor " + sec + " s";
  return "vor " + Math.round(sec/60) + " min ⚠";
}
function hhmm(minutes) {
  return String(Math.floor(minutes/60)).padStart(2,"0") + ":" + String(minutes%60).padStart(2,"0");
}
function minpvInfo() {
  if (s.mode !== "minpv") return "–";
  const minW = s.min_amps * WPA;
  if (s.minpv_low_s !== null) {
    const rest = Math.max(0, s.minpv_timeout_s - s.minpv_low_s);
    return "⛅ Wolkenloch – Stopp in " + rest + " s, Erholung ab "
      + Math.round(s.minpv_resume_factor * minW) + " W";
  }
  if (s.charging) return "lädt – Timeout ab < " + Math.round(s.minpv_pause_factor * minW) + " W";
  return "wartet – Start ab " + Math.round(s.minpv_start_factor * minW) + " W";
}
function boostInfo() {
  if (!s.boost_wh) return "aus";
  const rest = Math.max(0, s.boost_wh - s.boost_used_wh);
  const budget = (rest/1000).toFixed(1) + " / " + (s.boost_wh/1000).toFixed(1) + " kWh frei";
  if (s.soc !== null && s.soc < s.boost_min_soc)
    return "pausiert – SOC < " + s.boost_min_soc + " % (" + budget + ")";
  if (rest <= 0) return "Budget heute aufgebraucht";
  if (s.charging && s.battery_w !== null && s.battery_w < 0)
    return "🔋→🚗 " + Math.round(Math.min(-s.battery_w, s.boost_w)) + " W – " + budget;
  return "bereit, max " + s.boost_w + " W – " + budget;
}
function gridShare() {
  if (s.grid_w === null) return "–";
  const imp = Math.max(0, -s.grid_w);
  if (imp < 1) return "0 W – alles aus PV/Batterie";
  const txt = Math.round(imp) + " W ≈ " + (imp / WPA).toFixed(1) + " A";
  if (s.charge_w < 1) return txt + " (Haus)";
  return txt + " – " + Math.round(100 * Math.min(imp, s.charge_w) / s.charge_w) + " % der Ladung";
}
function heart(seen_s, limit) {
  const ok = seen_s !== null && seen_s !== undefined && seen_s < limit;
  return ok ? '<span class="heart">❤️</span>' : '<span class="heart dead">💔</span>';
}
async function refresh() {
  s = await (await fetch("/api/status")).json();
  const f = document.getElementById("frei");
  f.textContent = s.released ? "Freigegeben – tippen zum Sperren" : "GESPERRT – tippen zum Freigeben";
  f.className = s.released ? "on" : "warn";
  const row = r => `<div class="row"><span>${r[0]}</span><span class="val">${r[1]}</span></div>`;
  const boxLimit = Math.max(90, 3 * s.heartbeat_s);
  const rows = [
    ["Nachtfenster", s.night ? "🌙 AKTIV – lädt mit voller Leistung" : "inaktiv"],
    ["Netz", s.grid_w === null ? "–" : (s.grid_w >= 0 ? "Einspeisung " : "Bezug ") + Math.abs(s.grid_w) + " W"],
    ["Überschuss", Math.round(s.surplus_w) + " W ≈ " + (s.surplus_w / WPA).toFixed(1) + " A"],
    ["Ladeleistung", Math.round(s.charge_w) + " W ≈ " + (s.charge_w / WPA).toFixed(1) + " A"
      + (s.charge_w_src === "gemessen" || !s.charging ? "" : " (" + s.charge_w_src + ")")],
    ["Wallbox", heart(s.box_seen_s, boxLimit) + " "
      + (s.box_connected ? (s.charging ? "⚡ lädt" : "🔌 verbunden") : "⛔ getrennt")],
  ];
  document.getElementById("stat").innerHTML = rows.map(row).join("");
  const drows = [
    ["Box-Status (OCPP)", s.box_status],
    ["Limit", s.current_limit.toFixed(1) + " A"],
    ["PV-Überschuss echt", s.pv_surplus_w === null ? "–"
      : Math.round(s.pv_surplus_w) + " W ≈ " + (s.pv_surplus_w / WPA).toFixed(1) + " A"],
    ["minpv-Hysterese", minpvInfo()],
    ["Netzanteil", gridShare()],
    ["PV-Mittel " + (s.avg_active_s ? Math.round(s.avg_active_s/60) + " min" : "aus")
      + (s.charging ? "" : " (Start)"),
      s.pv_avg_w === null ? "–"
      : Math.round(s.pv_avg_w) + " W ≈ " + (s.pv_avg_w / WPA).toFixed(1) + " A"],
    ["Batterie-Boost", boostInfo()],
    ["Heartbeat Box", ago(s.box_seen_s)],
    ["Heartbeat Huawei", ago(s.huawei_seen_s)],
  ];
  document.getElementById("dbgstat").innerHTML = drows.map(row).join("");
  document.getElementById("hheart").innerHTML = heart(s.huawei_seen_s, 90);
  const brows = [
    ["Batterie-SOC", s.soc === null ? "–" : s.soc.toFixed(0) + " %"],
    ["Batterie", s.battery_w === null ? "–"
      : (s.battery_w >= 0 ? "lädt " : "entlädt ") + Math.abs(Math.round(s.battery_w)) + " W"],
    ["Netzladung", s.battery_grid_charge
      ? "⚡ AKTIV" + (s.battery_charge_auto ? " (Automatik)" : "") + " bis " + s.batt_target_soc + " %"
      : (s.forcible_cmd === 1 ? "aktiv (extern gestartet)" : "aus")],
    ["Prognose " + s.forecast_label, s.forecast_kwh === null ? "–"
      : (s.forecast_kwh < s.forecast_min_kwh ? "🌥 " : "☀️ ") + s.forecast_kwh.toFixed(1) + " kWh"],
    ["Netz", s.grid_w === null ? "–" : (s.grid_w >= 0 ? "Einspeisung " : "Bezug ") + Math.abs(s.grid_w) + " W"],
  ];
  document.getElementById("batstat").innerHTML = brows.map(row).join("");
  for (const m of ["pv","minpv","fast"])
    document.getElementById("m_"+m).classList.toggle("on", s.mode === m);
  const mi = document.getElementById("minamps");
  if (document.activeElement !== mi) {
    mi.value = s.min_amps;
    document.getElementById("minlabel").textContent = s.min_amps;
    document.getElementById("minlabel2").textContent = s.min_amps;
  }
  const hb = document.getElementById("heartbeat");
  if (document.activeElement !== hb) {
    hb.value = s.heartbeat_s;
    document.getElementById("hblabel").textContent = s.heartbeat_s;
  }
  const nb = document.getElementById("night");
  nb.classList.toggle("on", s.night_enabled);
  nb.textContent = "Nachtladen " + (s.night_enabled ? "aktiv" : "aus") + " – nachts ab "
    + hhmm(s.night_start_min) + " bis " + hhmm(s.night_end_min) + " mit voller Leistung";
  const bb = document.getElementById("batt");
  bb.classList.toggle("on", s.battery_grid_charge);
  bb.textContent = s.battery_grid_charge
    ? "Batterie-Netzladung stoppen (lädt mit " + s.batt_charge_w + " W bis " + s.batt_target_soc + " %)"
    : "Batterie-Netzladung starten (Test: " + s.batt_charge_w + " W bis " + s.batt_target_soc + " %)";
}
async function toggleBatt() {
  const r = await fetch("/api/battery/"+(s.battery_grid_charge?"off":"on"), {method:"POST"});
  if (!r.ok) alert(await r.text());
  refresh();
}
async function setMode(m) { await fetch("/api/mode/"+m, {method:"POST"}); refresh(); }
async function toggleRelease() {
  await fetch("/api/release/"+(s.released?"off":"on"), {method:"POST"}); refresh();
}
async function toggleNight() { await setConfig({night_enabled: !s.night_enabled}); }
async function setConfig(cfg) {
  await fetch("/api/config", {method:"POST", headers:{"Content-Type":"application/json"},
                              body: JSON.stringify(cfg)});
  refresh();
}
const pg = document.getElementById("pages");
pg.addEventListener("scroll", () => {
  const i = Math.round(pg.scrollLeft / pg.clientWidth);
  document.querySelectorAll(".dot").forEach((d, n) => d.classList.toggle("on", n === i));
});
refresh(); setInterval(refresh, 2000);
</script></body></html>"""


async def http_index(_request):
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def http_status(_request):
    return web.json_response({
        "mode": state.mode,
        "released": state.released,
        "min_amps": state.min_amps,
        "night_enabled": state.night_enabled,
        "heartbeat_s": state.heartbeat_s,
        "night_start_min": state.night_start_min,
        "night_end_min": state.night_end_min,
        "grid_w": state.grid_w,
        "soc": state.soc,
        "battery_w": state.battery_w,
        "batt_target_soc": state.batt_target_soc,
        "batt_charge_w": state.batt_charge_w,
        "forcible_cmd": state.forcible_cmd,
        "battery_charge_auto": state.battery_charge_auto,
        "forecast_label": relevant_forecast()[0],
        "forecast_kwh": relevant_forecast()[1],
        "forecast_min_kwh": state.forecast_min_kwh,
        "charge_w": state.charge_w,
        "charge_w_src": state.charge_w_src,
        "surplus_w": (state.grid_w or 0) + state.charge_w,
        "pv_surplus_w": None if state.grid_w is None
                        else state.grid_w + state.charge_w + (state.battery_w or 0),
        "minpv_low_s": None if state.minpv_low_since is None
                       else round(asyncio.get_event_loop().time() - state.minpv_low_since),
        "pv_avg_w": state.pv_avg_w,
        "avg_window_s": state.avg_window_s,
        "avg_active_s": state.avg_active_s,
        "boost_w": state.boost_w,
        "boost_wh": state.boost_wh,
        "boost_used_wh": round(state.boost_used_wh),
        "boost_min_soc": state.boost_min_soc,
        "minpv_timeout_s": state.minpv_timeout_s,
        "minpv_start_factor": state.minpv_start_factor,
        "minpv_pause_factor": state.minpv_pause_factor,
        "minpv_resume_factor": state.minpv_resume_factor,
        "current_limit": state.current_limit,
        "charging": state.charging,
        "night": in_night_window(),
        "battery_grid_charge": state.battery_grid_charge,
        "box_connected": charge_point is not None,
        "box_status": state.box_status,
        "box_seen_s": None if state.last_box_seen is None
                      else round(datetime.datetime.now().timestamp() - state.last_box_seen),
        "huawei_seen_s": None if state.last_huawei_seen is None
                         else round(datetime.datetime.now().timestamp() - state.last_huawei_seen),
    })


async def http_mode(request):
    mode = request.match_info["mode"]
    if mode not in ("pv", "minpv", "fast"):
        raise web.HTTPBadRequest(text="mode muss pv|minpv|fast sein")
    state.mode = mode
    state.surplus_since = state.deficit_since = state.minpv_low_since = None
    log.info("Modus (Web): %s", mode)
    return web.json_response({"ok": True})


async def http_release(request):
    state.released = request.match_info["onoff"] == "on"
    log.info("Freigabe (Web): %s", "erteilt" if state.released else "zurückgenommen")
    return web.json_response({"ok": True})


async def http_battery(request):
    if request.match_info["onoff"] == "on":
        error = await start_battery_grid_charge()
    else:
        error = await stop_battery_grid_charge()
    if error:
        raise web.HTTPServiceUnavailable(text=error)
    return web.json_response({"ok": True})


async def http_config(request):
    cfg = await request.json()
    if "min_amps" in cfg:
        amps = int(cfg["min_amps"])
        if not MIN_AMPS <= amps <= MAX_AMPS:
            raise web.HTTPBadRequest(text=f"min_amps muss {MIN_AMPS}–{MAX_AMPS} sein")
        state.min_amps = amps
        log.info("Min-Ampere (Web): %s A", amps)
    if "night_enabled" in cfg:
        state.night_enabled = bool(cfg["night_enabled"])
        log.info("Nachtladen (Web): %s", "aktiv" if state.night_enabled else "aus")
    if "heartbeat_s" in cfg:
        hb = int(cfg["heartbeat_s"])
        if not 5 <= hb <= 300:
            raise web.HTTPBadRequest(text="heartbeat_s muss 5–300 sein")
        state.heartbeat_s = hb
        log.info("Heartbeat (Web): %s s", hb)
        if charge_point is not None:
            await charge_point.change_configuration("HeartbeatInterval", str(hb))
    return web.json_response({"ok": True})


async def web_task(port: int):
    app = web.Application()
    app.router.add_get("/", http_index)
    app.router.add_get("/api/status", http_status)
    app.router.add_post("/api/mode/{mode}", http_mode)
    app.router.add_post("/api/release/{onoff}", http_release)
    app.router.add_post("/api/battery/{onoff}", http_battery)
    app.router.add_post("/api/config", http_config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Web-Interface: http://0.0.0.0:%s/", port)
    await asyncio.Event().wait()


def parse_hhmm(text: str) -> int:
    try:
        hours, _, minutes = text.strip().partition(":")
        result = int(hours) * 60 + int(minutes or 0)
        if not 0 <= result < 24 * 60:
            raise ValueError
        return result
    except ValueError:
        sys.exit(f"Ungültige Uhrzeit {text!r} — erwartet HH:MM (z. B. 22:30)")


def load_dotenv():
    """Minimaler .env-Lader (Projektverzeichnis), ohne Zusatz-Dependency."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


async def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inverter", default=os.environ.get("PVUEB_INVERTER_IP"))
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--web-port", type=int, default=8080)
    args = parser.parse_args()
    if not args.inverter:
        sys.exit("Wechselrichter-IP fehlt: --inverter, PVUEB_INVERTER_IP oder .env setzen")
    state.heartbeat_s = int(os.environ.get("PVUEB_HEARTBEAT_S", "10"))
    state.night_start_min = parse_hhmm(os.environ.get("PVUEB_NIGHT_START", "00:00"))
    state.night_end_min = parse_hhmm(os.environ.get("PVUEB_NIGHT_END", "08:00"))
    state.batt_low_soc = float(os.environ.get("PVUEB_BATT_LOW_SOC", "30"))
    state.batt_target_soc = float(os.environ.get("PVUEB_BATT_TARGET_SOC", "80"))
    state.batt_charge_w = int(os.environ.get("PVUEB_BATT_CHARGE_W", "500"))
    if not 0 < state.batt_low_soc < state.batt_target_soc <= 100:
        sys.exit("PVUEB_BATT_LOW_SOC/TARGET_SOC unplausibel (0 < low < target <= 100 nötig)")
    state.limit_step_a = float(os.environ.get("PVUEB_LIMIT_STEP_A", "0.1"))
    state.limit_deadband_a = float(os.environ.get("PVUEB_LIMIT_DEADBAND_A", "0.3"))
    if not 0 < state.limit_step_a <= 1 or state.limit_deadband_a < state.limit_step_a:
        sys.exit("PVUEB_LIMIT_STEP_A muss in (0, 1] liegen, DEADBAND >= STEP")
    state.avg_window_s = int(float(os.environ.get("PVUEB_AVG_WINDOW_MIN", "10")) * 60)
    state.start_avg_window_s = int(float(os.environ.get("PVUEB_START_AVG_MIN", "2")) * 60)
    if state.avg_window_s < 0 or state.start_avg_window_s < 0:
        sys.exit("PVUEB_AVG_WINDOW_MIN und PVUEB_START_AVG_MIN müssen >= 0 sein")
    state.boost_w = int(os.environ.get("PVUEB_BOOST_W", "2500"))
    state.boost_wh = int(float(os.environ.get("PVUEB_BOOST_H", "2")) * state.boost_w)
    state.boost_min_soc = float(os.environ.get("PVUEB_BOOST_MIN_SOC", "30"))
    if state.boost_w < 0 or state.boost_wh < 0 or not 0 <= state.boost_min_soc <= 100:
        sys.exit("PVUEB_BOOST_W/_H müssen >= 0 sein, PVUEB_BOOST_MIN_SOC 0–100")
    state.poll_interval_s = int(os.environ.get("PVUEB_POLL_INTERVAL_S", "5"))
    state.adjust_min_interval_s = int(os.environ.get("PVUEB_ADJUST_MIN_INTERVAL_S", "25"))
    state.start_delay_s = int(os.environ.get("PVUEB_START_DELAY_S", "120"))
    state.stop_delay_s = int(os.environ.get("PVUEB_STOP_DELAY_S", "180"))
    state.minpv_start_factor = float(os.environ.get("PVUEB_MINPV_START_FACTOR", "1.10"))
    state.minpv_pause_factor = float(os.environ.get("PVUEB_MINPV_PAUSE_FACTOR", "0.75"))
    state.minpv_resume_factor = float(os.environ.get("PVUEB_MINPV_RESUME_FACTOR", "0.90"))
    state.minpv_timeout_s = int(float(os.environ.get("PVUEB_MINPV_TIMEOUT_MIN", "10")) * 60)
    state.charge_w_max_age_s = int(os.environ.get("PVUEB_CHARGE_W_MAX_AGE_S", "30"))
    if state.charge_w_max_age_s < 1:
        sys.exit("PVUEB_CHARGE_W_MAX_AGE_S muss >= 1 Sekunde sein")
    if not 0 < state.minpv_pause_factor < state.minpv_resume_factor <= state.minpv_start_factor:
        sys.exit("PVUEB_MINPV_*_FACTOR unplausibel (0 < pause < resume <= start nötig)")
    if min(state.poll_interval_s, state.adjust_min_interval_s,
           state.start_delay_s, state.stop_delay_s) < 1:
        sys.exit("Regelzeit-Parameter (PVUEB_*_S) müssen >= 1 Sekunde sein")
    state.lat = float(os.environ.get("PVUEB_LAT", "52.25"))
    state.lon = float(os.environ.get("PVUEB_LON", "10.66"))
    state.pv_tilt = int(os.environ.get("PVUEB_PV_TILT", "42"))
    state.pv_azimut = int(os.environ.get("PVUEB_PV_AZIMUT", "0"))
    state.pv_kwp = float(os.environ.get("PVUEB_PV_KWP", "7"))
    state.forecast_min_kwh = float(os.environ.get("PVUEB_FORECAST_MIN_KWH", "5"))
    if state.night_start_min == state.night_end_min:
        sys.exit("PVUEB_NIGHT_START und PVUEB_NIGHT_END dürfen nicht gleich sein")

    server = await websockets.serve(on_connect, "0.0.0.0", args.port, subprotocols=["ocpp1.6"])
    log.info("Regel-Loop läuft. OCPP auf ws://0.0.0.0:%s/, Wechselrichter %s", args.port, args.inverter)
    await asyncio.gather(
        server.wait_closed(), modbus_task(args.inverter), control_task(),
        command_loop(), web_task(args.web_port), forecast_task(),
    )


if __name__ == "__main__":
    asyncio.run(main())
