#!/usr/bin/env python3
"""M3-PoC: Regel-Loop — verbindet Sun2000-Messung mit Pulsar-Plus-Steuerung.

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
sähe der Regler beim Ladestart einen PV-Einbruch, der keiner ist. Die
Schätzung ist auf PV-Erzeugung minus Netz minus Batterie gedeckelt (das Haus
verbraucht nie negativ), damit eine Ladung, die gar nicht stattfindet, nicht
dauerhaft einen Überschuss vortäuscht.

Ladeleistung folgt dem gleitenden Mittel des echten PV-Überschusses (inkl.
Batterie-Ladeleistung, die das Auto bekommen kann), gedeckelt auf den
Momentanwert plus Batterie-Boost. Zwei Fenster: PVUEB_AVG_WINDOW_MIN beim
Regeln der laufenden Ladung, PVUEB_START_AVG_MIN (kurz) für die
Startentscheidung, damit der Start nicht dem trägen Mittel hinterherhinkt.
Batterie-Boost (PVUEB_BOOST_*): bricht die PV-Leistung
kurz ein (Wolke), schiebt die Hausbatterie bis zu PVUEB_BOOST_W nach, statt die
Ladung herunterzuregeln — Tagesbudget PVUEB_BOOST_W × PVUEB_BOOST_H, nur bei
laufender Ladung und SOC über PVUEB_BOOST_MIN_SOC. Nachgeladen wird die
Batterie danach aus PV oder nachts über die Netzladung. Dauer-Boost
(PVUEB_PERMA_BOOST_*): steht die Batterie über der oberen SOC-Schwelle, kann
sie ohnehin nichts mehr aufnehmen — sie schiebt dann dauerhaft eine feste
Leistung ins Auto, bis die untere Schwelle erreicht ist. Alle Boosts zusammen
sind auf PVUEB_BATT_MAX_W gedeckelt, die Entladegrenze der Batterie; was der
Wechselrichter nicht liefern kann, käme sonst aus dem Netz. Starthilfe: ist die
Batterie voll genug (SOC über PVUEB_BOOST_START_SOC), füllt sie bis zu
PVUEB_BOOST_START_W an der Startschwelle fehlende Leistung auf — sonst bliebe
die Ladung wegen einer Handvoll Watt aus. Nur die Lücke, nicht pauschal: liegt
die PV über der Schwelle, kostet die Starthilfe nichts. Bei laufender Ladung
füllt sie bis zur Mindestleistung, damit der Start nicht direkt in die
Stopp-Schwelle zurückfällt.

Batterie-Netzladung (LUNA2000-Zwangsladung, Register verifiziert 2026-07-16):
lädt mit PVUEB_BATT_CHARGE_W bis PVUEB_BATT_TARGET_SOC, der Wechselrichter
stoppt am Ziel selbst. Manuell jederzeit über den Web-Toggle; Automatik
startet höchstens einmal pro Nacht, wenn Nachtfenster aktiv, SOC unter
PVUEB_BATT_LOW_SOC und die Sonnenprognose (forecast.solar) unter
PVUEB_FORECAST_MIN_KWH liegt.

Ladeleistung als Referenz (PVUEB_WALLBOX_*, optional): Die Box meldet ihre
Leistung nicht über OCPP, liefert sie aber an die Hersteller-Cloud. Von dort
geholt dient sie ausschließlich der Kontrolle — die Regelung rührt den Wert
nicht an (30 s Aktualisierung gegen 5 s Regeltakt, und ein Internetausfall
darf keine Ladung beeinflussen). Status und Mitschnitt führen die Differenz
zur Schätzung als charge_w_abweichung mit.

Die .env wird beim Start und nach Änderungen im Web-UI vollständig
zurückgeschrieben — jede Einstellung mit dem wirksamen Wert und einer Erklärung,
Sicherung in .env.bak. Damit überleben auch Schieberegler-Werte den Neustart.
Liegt keine .env vor (Docker mit env_file), passiert nichts.

Ausfallsicherheit (docs/issue_nightly_load_did_not_work.md): Der Regeltakt ist
die einzige Stelle, die die Wallbox anfasst, und er darf nie aufhören. Drei
Ebenen sichern das ab — ein fehlgeschlagener Takt kostet einen Takt
(control_task), eine abgestürzte Daueraufgabe läuft neu an (bewacht), und
bleibt der Takt trotzdem aus, beendet ein Wächter-Thread den Prozess, damit
Docker neu startet (watchdog). Das Alter des letzten Takts steht in der UI und
im Healthcheck; ohne diese Zahl sieht ein scheintoter Dienst gesund aus.

Cloud-Daten sind strikt nachrangig: die myWallbox-Cloud liefert nur eine
Referenzmessung fürs Protokoll, die Sonnenprognose entscheidet allein über die
Batterie-Netzladung und fällt ohne Antwort auf „nicht laden" zurück. PV-Regelung
und Nachtfenster laufen ohne jede Internetverbindung.
"""

import argparse
import asyncio
import base64
import collections
import ctypes
import datetime
import hashlib
import json
import logging
import os
import secrets
import shutil
import re
import subprocess
import sys
import threading
import time

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


def software_version() -> dict:
    """Was hier gerade läuft: Commit, Beschreibung, Bauzeit.

    Im Container gibt es kein Git-Verzeichnis, deshalb zählen zuerst die beim
    Bauen gesetzten Variablen (Dockerfile-ARGs, siehe Makefile-Ziel `image`).
    Läuft der Dienst direkt aus dem Arbeitsverzeichnis, fragt er Git selbst —
    dann steht bei ungespeicherten Änderungen ein „-dirty" hinter dem Commit.
    """
    info = {"commit": os.environ.get("PVUEB_GIT_COMMIT", ""),
            "beschreibung": os.environ.get("PVUEB_GIT_DESCRIBE", ""),
            "gebaut": os.environ.get("PVUEB_BUILD_TIME", ""),
            "quelle": "Build" if os.environ.get("PVUEB_GIT_COMMIT") else "Git"}
    if not info["commit"]:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            ruf = ["git", "-C", repo, "describe", "--always", "--dirty", "--tags"]
            info["beschreibung"] = subprocess.run(
                ruf, capture_output=True, text=True, timeout=5).stdout.strip()
            info["commit"] = subprocess.run(
                ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if not info["commit"]:
        info["commit"], info["quelle"] = "unbekannt", "unbekannt"
    return info


VERSION = software_version()

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
REG_PV_POWER = 32080         # int32, W, AC-Wirkleistung des Wechselrichters
# Was die Module liefern (DC-Eingang der Strings). Erst damit stimmt die
# Hausbilanz: 32080 ist der AC-Ausgang und enthält die Batterie bereits — lädt
# sie, ist er kleiner als die Dachleistung, entlädt sie, größer.
REG_PV_DC_POWER = 32064      # int32, W, DC-Eingangsleistung
# Batterie (37001) und Netz (37113) am Stück lesen — nacheinander gelesen
# stammen sie aus verschiedenen Momenten und die Summe zappelt
REG_BLOCK_START, REG_BLOCK_COUNT = REG_BATTERY_POWER, 114

# LUNA2000-Zwangsladung (verifiziert 2026-07-16 gegen huawei_solar 3.0.6)
REG_FORCIBLE_CMD = 47100         # uint16: 0 = Stop, 1 = Laden, 2 = Entladen
REG_FORCIBLE_TARGET_SOC = 47101  # uint16, ×0,1 %
REG_FORCIBLE_MODE = 47246        # uint16: 0 = Dauer, 1 = Ziel-SOC
REG_FORCIBLE_CHARGE_W = 47247    # uint32, W

# Sonnenprognose (forecast.solar, kostenlos ohne Key; Anlagendaten aus .env)
FORECAST_URL = "https://api.forecast.solar/estimate/{lat}/{lon}/{tilt}/{azimut}/{kwp}"
FORECAST_REFRESH_S = 6 * 3600    # Abruf-Rhythmus (Limit wäre 12/h — wir sind weit drunter)
FORECAST_RETRY_S = 15 * 60       # Wartezeit nach Fehlversuch

# myWallbox-Cloud (api.wall-box.com; Zugang aus .env, PVUEB_WALLBOX_*).
# Nur Referenzmessung — die Regelung rührt diese Werte nicht an.
WALLBOX_TOKEN_TTL_S = 45         # JWT hält rund 60 s, vorher erneuern
WALLBOX_MIN_POLL_S = 30          # die Cloud aktualisiert nicht häufiger
WALLBOX_STATUS = {               # nur die Zustände, die im Betrieb vorkommen
    # 165/209/210 melden „locked" — die Autorisierung der Box selbst (App/RFID),
    # nicht die Freigabe in PVueb. Ohne aktiviertes RFID steht das dauerhaft und
    # verhindert nichts: die Ladung in der Nacht zum 23.07. lief über
    # RemoteStart trotzdem an. Der Text sagt deshalb nur, was der Fall ist.
    161: "bereit", 162: "bereit", 163: "kein Auto", 164: "wartet",
    165: "gesperrt",
    166: "aktualisiert", 177: "geplant", 178: "pausiert", 179: "geplant",
    180: "warte auf Auto", 181: "warte auf Auto", 182: "pausiert",
    183: "warte auf Auto", 184: "warte auf Auto", 185: "warte auf Auto",
    186: "warte auf Auto", 187: "warte auf Auto", 188: "warte auf Auto",
    189: "warte auf Auto", 193: "lädt", 194: "lädt", 195: "lädt",
    196: "entlädt", 209: "gesperrt, kein Auto",
    210: "gesperrt, Auto verbunden",
}
# Aufgabenschirm: eine gestorbene Nebenaufgabe darf nie die anderen mitreißen
TASK_RESTART_S = 5               # Wartezeit, bevor eine abgestürzte Aufgabe neu anläuft

# Watchdog: siehe watchdog(). Der Regeltakt setzt in jeder Runde seinen
# Herzschlag; bleibt er aus, endet der Prozess und Docker startet neu.
WATCHDOG_TIMEOUT_S = 120         # ~24 ausgefallene Takte, bevor abgebrochen wird
WATCHDOG_CHECK_S = 15            # Abstand der Prüfung im Wächter-Thread

# Verbindungswache zur Wallbox: siehe leitung_tot(). Eine stehende, aber
# stumme Leitung ist schlimmer als eine abgerissene — der Regeltakt läuft
# dann in jeden OCPP-Timeout hinein, statt die Box neu aufbauen zu lassen.
BOX_LINK_CHECK_S = 10            # Abstand der Prüfung
BOX_SILENCE_MIN_S = 45           # Untergrenze, auch bei sehr kurzem Heartbeat

# Sitzung über einen Neustart retten (PVUEB_SESSION_FILE). Der Regler hält
# Zustand, den ihm niemand zurückgeben kann — vor allem die ID der laufenden
# OCPP-Transaktion. Ohne sie kann er eine Ladung, die er nicht selbst gestartet
# hat, weder stoppen noch geordnet regeln: Wallbox, Wechselrichter und Auto
# laden weiter, nur der Regler steht daneben. Mit Sicherung merkt keines der
# drei Geräte etwas von einem Neustart.
SESSION_MAX_AGE_S = 600          # ältere Sicherung: Sitzung gilt als beendet
# Der Zeitstempel der Sicherung ist zugleich das Maß für die Ausfallzeit —
# der Takt muss deutlich feiner sein als die Grenze, sonst gilt ein Ausfall
# von sechs Minuten als elf und die Sitzung ginge verloren. Eine Datei von
# ~400 Byte je Minute fällt neben dem Mitschnitt (alle 10 s) nicht auf.
SESSION_SAVE_INTERVAL_S = 60     # zusätzlich zum Sichern bei jeder Änderung
SESSION_CLOCK_TOLERANCE_S = 5    # so viel Zeitstempel-Vorlauf ist Uhrendrift, kein Sprung
CLOCK_WAIT_MAX_S = 15            # so lange auf NTP warten, bevor die Sitzung bewertet wird
TIME_ERROR = 5                   # adjtimex(2): Uhr nicht synchronisiert (STA_UNSYNC)

# Meldestelle (PVUEB_NOTIFY_URL). PVueb kennt keinen Messenger: es legt ein
# JSON in eine Warteschlange und läuft weiter. Am 28.07.2026 regelte der Dienst
# 17 Stunden nicht mehr, und niemand erfuhr es — das ist der ganze Zweck.
#
# Die Warteschlange ist begrenzt und wirft Ältestes weg. Unbegrenzt wäre sie
# ein Speicherleck mit Anlauf: eine flatternde Quelle bei totem Empfänger füllt
# sie in Minuten. Der Empfänger hat einen Spool auf Platte — hier drin muss
# nur der Weg über einen Neustart des Messengers reichen.
NOTIFY_QUEUE_MAX = 200           # mehr Meldungen als das sind ohnehin ein Fehler
NOTIFY_SEND_CHECK_S = 5          # Takt der Zustellaufgabe
NOTIFY_MAX_AGE_S = 900           # danach ist eine Meldung wertlos und fliegt raus
NOTIFY_RETRY_MIN_S = 20          # erster Abstand nach einem Fehlversuch
NOTIFY_RETRY_MAX_S = 300         # Obergrenze des wachsenden Abstands
MELDE_CHECK_S = 10               # Takt der Regelprüfung (Flanken, Offline-Fristen)
MELDE_SPERRE_S = 3600            # Wiederholsperre für dieselbe Lage beim Empfänger


class State:
    # Startverhalten ohne gültige Sicherung: freigegeben, PV-Minimum mit 6 A.
    # Das ist der Betriebsfall — die Anlage soll nach einem Strom- oder
    # Softwareausfall von allein weiterarbeiten, nicht auf einen Knopfdruck
    # warten. Wer das nicht will, nimmt die Freigabe im Web-UI zurück; sie
    # überlebt dann über die Sicherung.
    mode = "minpv"               # pv | minpv | fast
    released = True              # Freigabe für den Regler (im Web-UI umschaltbar)
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
    boost_start_w = 500          # so viel Lücke zur Startschwelle füllt die Batterie, 0 = aus (aus .env)
    boost_start_soc = 50.0       # Starthilfe erst ab diesem SOC (aus .env)
    batt_max_w = 2500            # was die Hausbatterie höchstens abgeben kann (aus .env)
    perma_boost_w = 1000         # Dauer-Boost aus voller Batterie, 0 = aus (aus .env)
    perma_boost_on_soc = 90.0    # ab diesem SOC springt er an (aus .env)
    perma_boost_off_soc = 50.0   # bis hierher darf er die Batterie leeren (aus .env)
    perma_boost_aktiv = False    # Hysterese-Merker zwischen den beiden Schwellen
    boost_used_wh = 0.0          # heute schon verbrauchtes Boost-Budget
    boost_day: datetime.date | None = None  # Tag, für den boost_used_wh gilt
    boost_last_t: float | None = None       # Loop-Zeit des letzten Boost-Takts
    batt_low_soc = 30.0          # Automatik-Startschwelle in % (aus .env)
    batt_target_soc = 80.0       # Netzladung bis zu diesem SOC (aus .env)
    batt_charge_w = 500          # Leistung der Zwangsladung in W (aus .env)
    lat = 52.27                  # Standort/Anlage für forecast.solar (aus .env)
    lon = 10.52
    pv_tilt = 42
    pv_azimut = 0                # forecast.solar-Konvention: 0 = Süd
    pv_kwp = 7.0
    forecast_min_kwh = 5.0       # „keine Sonne“ = Prognose unter diesem Wert (aus .env)
    forecast: dict[str, float] = {}      # Datum (ISO) -> prognostizierte kWh
    forecast_at: float | None = None     # Unix-Zeit des letzten erfolgreichen Abrufs
    grid_w: float | None = None  # positiv = Einspeisung
    soc: float | None = None     # Batterie-SOC in %
    battery_w: float | None = None  # Batterie-Leistung, positiv = Laden
    pv_w: float | None = None    # Wechselrichter-Ausgang (AC, Batterie schon verrechnet)
    pv_dc_w: float | None = None # was die Module liefern (DC-Eingang, Register 32064)
    modbus_block = True          # Batterie+Netz am Stück lesbar (SDongle-abhängig)
    charge_w = 0.0               # letzte bekannte Ladeleistung
    charge_w_seen: float | None = None   # Loop-Zeit der letzten Messung dazu
    charge_w_src = "keine"       # gemessen | Energie | geschätzt | keine (fürs UI)
    charge_w_max_age_s = 30      # ältere Messung gilt als tot (aus .env)
    charge_energy_wh: float | None = None   # letzter Zählerstand der Box
    charge_energy_t: float | None = None    # Loop-Zeit dazu
    charging = False
    # Die ID der laufenden OCPP-Transaktion. Sie steht bewusst hier und nicht
    # am ChargePoint: ohne sie lässt sich eine Ladung nicht stoppen, und sie
    # muss deshalb einen Neustart überleben (save_session).
    transaction_id: int | None = None
    current_limit = 0.0          # zuletzt gesetztes Limit in A (Raster limit_step_a)
    limit_known = False          # gilt current_limit noch? (nach Box-Boot: nein)
    limit_set_t = 0.0            # Loop-Zeit des letzten gesendeten Limits
    limit_step_a = 0.1           # Auflösung des Ladelimits in A (aus .env)
    limit_deadband_a = 0.3       # so viel Abweichung, bevor neu gesetzt wird (aus .env)
    limit_refresh_s = 300        # Limit spätestens so oft wiederholen (aus .env)
    limit_warn_factor = 0.6      # darunter gilt das Limit als wirkungslos (aus .env)
    limit_warned = False         # Warnung für diesen Ladevorgang schon raus
    start_retry_at: float | None = None  # frühester nächster Startversuch (Loop-Zeit)
    start_attempts = 0           # abgelehnte Startversuche in Folge
    start_retry_s = 30           # Basis für den Backoff zwischen Versuchen (aus .env)
    wake_tried = False           # Aufweckversuch für diese Startphase schon gemacht
    surplus_since: float | None = None   # Zeitstempel: Überschuss reicht seit ...
    deficit_since: float | None = None   # Zeitstempel: Überschuss fehlt seit ...
    minpv_low_since: float | None = None # Zeitstempel: PV-Überschuss unter Pause-Schwelle seit ...
    last_adjust = 0.0
    battery_grid_charge = False  # von uns gestartete Zwangsladung aktiv
    forcible_cmd: int | None = None      # gelesenes Register 47100 (0/1/2)
    battery_charge_auto = False  # aktive Zwangsladung kam von der Nachtautomatik
    battery_auto_started = False # Automatik hat diese Nacht schon gestartet (einmal pro Nacht)
    web_user = ""                # Basic Auth für die Web-UI, leer = offen (aus .env)
    web_password = ""
    ocpp_user = ""               # Basic Auth für den OCPP-Endpunkt, leer = offen (aus .env)
    ocpp_password = ""
    record_dir = ""              # Zielordner für den Mitschnitt, leer = aus (aus .env)
    record_interval_s = 10       # Abstand der mitgeschriebenen Zeilen (aus .env)
    record_keep_days = 14        # Ringbuffer: ältere Tage werden gelöscht (aus .env)
    last_box_seen: float | None = None     # Unix-Zeit: letzter OCPP-Heartbeat
    last_huawei_seen: float | None = None  # Unix-Zeit: letzter erfolgreicher Modbus-Poll
    box_status = "unbekannt"               # letzter StatusNotification-Status der Box
    # Herzschlag des Regeltakts, monotone Uhr. Nur diese eine Zahl entscheidet,
    # ob der Regler lebt — der Wächter-Thread liest sie, sonst niemand.
    last_tick: float = 0.0
    tick_error: str | None = None          # letzter Fehler aus einem Regeltakt, für die UI
    watchdog_s = WATCHDOG_TIMEOUT_S        # Geduld des Wächters in Sekunden (aus .env)
    wallbox_user = ""            # myWallbox-Konto, leer = Abfrage aus (aus .env)
    wallbox_password = ""
    wallbox_id = ""              # Charger-ID aus der App bzw. my.wall-box.com (aus .env)
    wallbox_poll_s = 60          # Abstand der Cloud-Abrufe (aus .env)
    wallbox: dict = {}           # letzter Snapshot, siehe wallbox_snapshot()
    wallbox_error: str | None = None       # letzter Fehler, für die UI
    last_wallbox_ok: float | None = None   # Unix-Zeit des letzten erfolgreichen Abrufs
    session_file = ""            # Ablage der Sitzungssicherung, leer = Standardpfad (aus .env)
    session_note = "frischer Start"        # was der Start aus der Sicherung machte, fürs UI
    started_at: float = 0.0                # Unix-Zeit des Prozessstarts
    notify_url = ""              # Meldestelle, leer = aus (aus .env)
    notify_timeout_s = 5         # Zeitgrenze je Zustellversuch (aus .env)
    melde_offline_s = 300        # so lange darf ein Gerät weg sein, bevor Alarm (aus .env)
    notify_error: str | None = None        # letzter Zustellfehler, nur fürs UI
    notify_sent = 0                        # zugestellte Meldungen
    notify_dropped = 0                     # verworfene (zu alt oder Warteschlange voll)


def session_path() -> str:
    """Wo die Sitzungssicherung liegt.

    Ohne PVUEB_SESSION_FILE bevorzugt der Mitschnitt-Ordner: der liegt im
    Container auf einem Volume und überlebt damit auch ein `up --build`, das
    den Container selbst wegwirft. Sonst neben dem Skript.
    """
    if state.session_file:
        return state.session_file
    if state.record_dir:
        return os.path.join(state.record_dir, "session.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session.json")


def system_marken() -> tuple[str | None, float | None]:
    """Boot-Kennung und Systemlaufzeit — Anker gegen eine springende Uhr.

    Der Pi hat keine gepufferte Uhr. Nach einem Stromausfall startet er mit
    der Zeit, die fake-hwclock zuletzt gemerkt hat, und springt erst mit dem
    ersten NTP-Kontakt auf die Wirklichkeit. In diesem Fenster ist jede
    Differenz zweier Wanduhr-Zeitstempel wertlos: Eine drei Stunden alte
    Sicherung sähe taufrisch aus.

    Die Boot-Kennung wechselt bei jedem Systemstart, die Laufzeit steigt
    monoton. Zusammen sagen sie unabhängig von der Uhr, wieviel Zeit seit dem
    Schreiben mindestens vergangen ist. Im Container kommen beide vom Host —
    ein Container-Neustart zählt damit richtigerweise als derselbe Boot.
    """
    try:
        with open("/proc/sys/kernel/random/boot_id") as fh:
            boot_id = fh.read().strip()
    except OSError:
        boot_id = None
    try:
        with open("/proc/uptime") as fh:
            uptime = float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        uptime = None
    return boot_id, uptime


def uhr_synchron() -> bool | None:
    """Hält der Kernel seine Uhr für diszipliniert? True/False, None = keine Auskunft.

    `adjtimex(2)` mit einem genullten Puffer ist ein reiner Lesezugriff (modes=0)
    und antwortet mit dem Zeitzustand; TIME_ERROR (5) bedeutet STA_UNSYNC, die
    Uhr läuft also frei. Der Weg funktioniert auch im Container: Die Systemzeit
    gehört dem Kernel des Hosts, ein NTP-Client im Image wäre weder nötig noch
    hilfreich. `timedatectl` scheidet gerade deshalb aus — es setzt systemd im
    Container voraus.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        puffer = ctypes.create_string_buffer(512)   # großzügig; struct timex ist kleiner
        zustand = libc.adjtimex(puffer)
    except (OSError, AttributeError):
        return None
    if zustand < 0:
        return None
    return zustand != TIME_ERROR


async def auf_uhr_warten(max_s: int = CLOCK_WAIT_MAX_S):
    """Vor der Sitzungsbewertung kurz auf NTP warten.

    Nach einem Stromausfall startet der Pi mit der Zeit von fake-hwclock. Ein
    paar Sekunden später steht die echte — dann lässt sich das Alter der
    Sicherung exakt bestimmen, statt es über die Systemlaufzeit nur nach unten
    abzuschätzen. Gewartet wird nur, wenn es überhaupt etwas zu bewerten gibt.
    """
    if not os.path.exists(session_path()) or uhr_synchron() is not False:
        return
    log.info("Systemuhr läuft ohne NTP — warte bis zu %d s, bevor die Sitzung bewertet wird",
             max_s)
    for _ in range(max_s):
        await asyncio.sleep(1)
        if uhr_synchron():
            log.info("Systemuhr synchronisiert")
            return
    log.warning("Systemuhr nach %d s ohne NTP — Sitzungsalter wird nur nach unten "
                "abgeschätzt", max_s)


def session_alter(daten: dict) -> tuple[float, str]:
    """Wie alt die Sicherung wirklich ist, in Sekunden, plus Begründung.

    Vier Fälle, vom verlässlichsten zum schwächsten:
    1. Gleicher Boot: die Differenz der Systemlaufzeiten ist exakt und von
       der Wanduhr unabhängig. Das ist der Normalfall (Dienst- oder
       Container-Neustart ohne Reboot).
    2. Reboot, aber die Uhr war beim Sichern von NTP diszipliniert und ist es
       jetzt wieder: dann ist die Wanduhr-Differenz echt — auch über einen
       Stromausfall hinweg. Nur so ist dessen Dauer überhaupt messbar.
    3. Reboot mit unsicherer Uhr: seit dem Systemstart ist mindestens die
       aktuelle Laufzeit vergangen, und der Ausfall begann davor. Es gilt der
       größere der beiden Werte — so kann eine zurückgestellte Uhr nichts
       verjüngen, auch wenn die echte Dauer unbekannt bleibt.
    4. Ohne /proc: nur die Wanduhr.
    """
    wanduhr = datetime.datetime.now().timestamp() - float(daten.get("saved_at") or 0)
    boot_id, uptime = system_marken()
    gespeichert_boot = daten.get("boot_id")
    gespeichert_uptime = daten.get("uptime_s")
    if (boot_id and gespeichert_boot == boot_id
            and uptime is not None and gespeichert_uptime is not None):
        return uptime - float(gespeichert_uptime), "Systemlaufzeit"
    if daten.get("clock_synced") and uhr_synchron():
        return wanduhr, "NTP-Uhr"
    if uptime is not None:
        return max(wanduhr, uptime), "Uhr ohne NTP, Untergrenze Systemlaufzeit"
    return wanduhr, "Wanduhr, ungeprüft"


def session_pruefsumme(rumpf: str) -> str:
    return hashlib.sha256(rumpf.encode()).hexdigest()


def session_status() -> str:
    """Die Sicherung jetzt prüfen: heil und wie alt? Für die Info-Seite.

    Beantwortet im laufenden Betrieb dieselben zwei Fragen, die beim Start
    über die Übernahme entscheiden — ohne dafür einen Neustart zu brauchen.
    """
    pfad = session_path()
    try:
        with open(pfad) as fh:
            kopf, _, rumpf = fh.read().partition("\n")
    except FileNotFoundError:
        return "fehlt"
    except OSError as exc:
        return f"unlesbar: {exc}"
    if not kopf.startswith("sha256:"):
        return "⛔ ohne Prüfsumme"
    rumpf = rumpf.strip()
    if not secrets.compare_digest(kopf[len("sha256:"):].strip(), session_pruefsumme(rumpf)):
        return "⛔ Prüfsumme falsch"
    try:
        daten = json.loads(rumpf)
    except ValueError as exc:
        return f"⛔ unlesbar: {exc}"
    alter, quelle = session_alter(daten)
    frisch = "✅" if -SESSION_CLOCK_TOLERANCE_S <= alter <= SESSION_MAX_AGE_S else "⛔"
    return (f"{frisch} heil, {max(0, round(alter))} s alt ({quelle}), "
            f"Grenze {SESSION_MAX_AGE_S} s")


def save_session():
    """Laufzeitzustand sichern. Fehler dürfen den Regler nie stoppen."""
    boot_id, uptime = system_marken()
    daten = {
        "saved_at": datetime.datetime.now().timestamp(),
        "boot_id": boot_id,
        "uptime_s": uptime,
        # Ohne diesen Vermerk ließe sich später nicht mehr sagen, ob der
        # Zeitstempel daneben von einer disziplinierten Uhr stammt
        "clock_synced": uhr_synchron(),
        "commit": VERSION.get("commit"),
        "mode": state.mode,
        "released": state.released,
        "min_amps": state.min_amps,
        "night_enabled": state.night_enabled,
        "heartbeat_s": state.heartbeat_s,
        "charging": state.charging,
        "transaction_id": state.transaction_id,
        "box_status": state.box_status,
        "current_limit": state.current_limit,
        "boost_used_wh": state.boost_used_wh,
        "boost_day": state.boost_day.isoformat() if state.boost_day else None,
        # Der Hysterese-Merker: ohne ihn stünde der Dauer-Boost nach einem
        # Neustart bei 70 % SOC still, bis die Batterie wieder 90 % erreicht
        "perma_boost_aktiv": state.perma_boost_aktiv,
        "battery_grid_charge": state.battery_grid_charge,
        "battery_charge_auto": state.battery_charge_auto,
        "battery_auto_started": state.battery_auto_started,
    }
    pfad = session_path()
    rumpf = json.dumps(daten)
    try:
        os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
        # Erst daneben schreiben, dann umbenennen: ein Stromausfall mitten im
        # Schreiben hinterlässt sonst eine halbe Datei, die beim nächsten Start
        # nur Fragen aufwirft. Die Prüfsumme in Zeile 1 deckt den Rest ab —
        # umgekippte Bits auf der SD-Karte oder einen Block, den der Controller
        # aus seinem Cache nur halb zurückgeschrieben hat.
        tmp = pfad + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(f"sha256:{session_pruefsumme(rumpf)}\n{rumpf}\n")
            # Ohne fsync liegt der Inhalt nur im Seitencache: der Rename wäre
            # nach einem Stromausfall sichtbar, die Daten dahinter nicht — und
            # gelesen würde die vorige Fassung. Genau der Zombie, der hier
            # nicht auferstehen soll.
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, pfad)
        # Auch der Verzeichniseintrag selbst muss auf die Karte, sonst zeigt
        # das Verzeichnis nach einem Stromausfall weiter auf die alte Datei.
        ordner = os.open(os.path.dirname(pfad) or ".", os.O_RDONLY)
        try:
            os.fsync(ordner)
        finally:
            os.close(ordner)
    except OSError as exc:
        log.warning("Sitzung konnte nicht gesichert werden (%s): %s", pfad, exc)


def load_session():
    """Sicherung einlesen und übernehmen, falls sie frisch genug ist.

    Frisch heißt: höchstens SESSION_MAX_AGE_S alt. Alles darüber ist keine
    unterbrochene Sitzung mehr, sondern eine beendete — dann startet der Regler
    mit den Vorgaben (freigegeben, minpv, 6 A) und fasst nichts an, was er
    nicht kennt.
    """
    pfad = session_path()
    try:
        with open(pfad) as fh:
            kopf, _, rumpf = fh.read().partition("\n")
    except FileNotFoundError:
        state.session_note = "keine Sicherung vorhanden — frischer Start"
        log.info("Keine Sitzungssicherung (%s) — frischer Start", pfad)
        return
    except OSError as exc:
        state.session_note = f"Sicherung unlesbar: {exc}"
        log.warning("Sitzungssicherung unlesbar (%s): %s", pfad, exc)
        return

    # Prüfsumme vor dem Parsen: eine Datei, die zwar gültiges JSON ergibt, aber
    # nicht die ist, die zuletzt geschrieben wurde, wäre sonst nicht zu erkennen.
    rumpf = rumpf.strip()
    if not kopf.startswith("sha256:"):
        state.session_note = "Sicherung ohne Prüfsumme — verworfen"
        log.warning("Sitzungssicherung ohne Prüfsumme (%s) — verworfen", pfad)
        return
    erwartet = kopf[len("sha256:"):].strip()
    tatsaechlich = session_pruefsumme(rumpf)
    if not secrets.compare_digest(erwartet, tatsaechlich):
        state.session_note = "Prüfsumme falsch — Sicherung verworfen"
        log.warning("Sitzungssicherung beschädigt (%s):\n  erwartet  %s\n  berechnet %s",
                    pfad, erwartet, tatsaechlich)
        return
    try:
        daten = json.loads(rumpf)
    except ValueError as exc:
        state.session_note = f"Sicherung unlesbar: {exc}"
        log.warning("Sitzungssicherung unlesbar (%s): %s", pfad, exc)
        return

    ausfall, quelle = session_alter(daten)
    if ausfall < -SESSION_CLOCK_TOLERANCE_S:
        # Zeitstempel aus der Zukunft: die Uhr ist gesprungen, das Alter sagt
        # nichts mehr. Lieber neu anfangen als eine alte Lage fortschreiben.
        state.session_note = f"Sicherung liegt {-ausfall / 60:.0f} min in der Zukunft — verworfen"
        log.warning("Sitzungssicherung datiert %.0f min in der Zukunft (%s) — verworfen",
                    -ausfall / 60, quelle)
        return
    if ausfall > SESSION_MAX_AGE_S:
        state.session_note = f"Sicherung verworfen ({ausfall / 60:.0f} min alt, {quelle})"
        log.info("Sitzungssicherung ist %.0f min alt (Grenze %d min, gemessen über %s) — verworfen",
                 ausfall / 60, SESSION_MAX_AGE_S // 60, quelle)
        return
    ausfall = max(0.0, ausfall)
    if daten.get("commit") and daten["commit"] != VERSION.get("commit"):
        log.info("Sicherung stammt aus Commit %s, hier läuft %s",
                 daten["commit"], VERSION.get("commit"))

    state.mode = daten.get("mode", state.mode)
    state.released = bool(daten.get("released", state.released))
    state.min_amps = int(daten.get("min_amps", state.min_amps))
    state.night_enabled = bool(daten.get("night_enabled", state.night_enabled))
    state.heartbeat_s = int(daten.get("heartbeat_s", state.heartbeat_s))
    state.charging = bool(daten.get("charging", False))
    state.transaction_id = daten.get("transaction_id")
    state.box_status = daten.get("box_status", state.box_status)
    state.current_limit = float(daten.get("current_limit", 0.0))
    # Was in der Box steht, hat sich in der Zwischenzeit ändern können — das
    # Limit gilt als unbekannt und wird im ersten Regeltakt neu gesetzt.
    state.limit_known = False
    state.boost_used_wh = float(daten.get("boost_used_wh", 0.0))
    tag = daten.get("boost_day")
    state.boost_day = datetime.date.fromisoformat(tag) if tag else None
    state.perma_boost_aktiv = bool(daten.get("perma_boost_aktiv", False))
    state.battery_grid_charge = bool(daten.get("battery_grid_charge", False))
    state.battery_charge_auto = bool(daten.get("battery_charge_auto", False))
    state.battery_auto_started = bool(daten.get("battery_auto_started", False))

    if state.charging and state.transaction_id is not None:
        state.session_note = (f"Ladung fortgesetzt (Transaktion {state.transaction_id}, "
                              f"{ausfall:.0f} s Ausfall, {quelle})")
        log.info("Sitzung übernommen: laufende Ladung, Transaktion %s, Ausfall %.0f s (%s) — "
                 "die Box lädt durch, der Regler greift wieder",
                 state.transaction_id, ausfall, quelle)
    else:
        state.session_note = f"Einstellungen übernommen ({ausfall:.0f} s Ausfall, {quelle})"
        log.info("Sitzung übernommen: keine laufende Ladung, Ausfall %.0f s (%s)",
                 ausfall, quelle)


async def session_task():
    """Regelmäßig sichern — die Änderungspunkte allein könnten etwas auslassen."""
    while True:
        await asyncio.sleep(SESSION_SAVE_INTERVAL_S)
        save_session()


def in_night_window(now: datetime.datetime | None = None) -> bool:
    if not state.night_enabled:
        return False
    t = now or datetime.datetime.now()
    minutes = t.hour * 60 + t.minute
    start, end = state.night_start_min, state.night_end_min
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end  # Fenster über Mitternacht (z. B. 23:00–08:00)


MAX_START_RETRY_S = 300      # Obergrenze für den Backoff zwischen Startversuchen


async def apply_limit(now: float, amps: float):
    """Limit an die Box schicken und den Zeitpunkt merken."""
    await charge_point.set_limit(amps)
    state.limit_set_t = now


def limit_due(now: float, amps: float) -> bool:
    """Muss das Limit raus, obwohl der Sollwert unverändert scheint?

    Drei Gründe, aus denen der Merker nicht reicht:
    ist er ungültig (Box gebootet, Setzen abgelehnt), weicht der Sollwert ab,
    oder liegt das letzte Setzen länger zurück als limit_refresh_s. Der letzte
    Fall ist der Schutz gegen stille Divergenz: übernimmt die Box ein Profil
    nicht oder überschreibt es jemand über die Hersteller-App, fällt das sonst
    nie auf — in der Nacht zum 2026-07-20 lud das Auto so 40 Minuten mit 6 A
    statt 16 A (docs/issue_limit_to_6A.md).
    """
    return (not state.limit_known
            or state.current_limit != amps
            or now - state.limit_set_t >= state.limit_refresh_s)


async def try_start(now: float, amps: float):
    """Ladung anstoßen, aber nicht im Regeltakt dagegenhämmern.

    Ob wirklich Strom fließt, entscheidet am Ende das Auto: es kann die
    Freigabe ignorieren (voll, Ladetimer, Ladeabbruch). Deshalb wird nach
    jedem Versuch gewartet, mit wachsendem Abstand — sonst liefe bei einem
    Auto, das nicht will, ein RemoteStart alle 5 Sekunden.
    """
    if state.start_retry_at is not None and now < state.start_retry_at:
        return
    # SuspendedEV heißt: die Box gibt frei, das Fahrzeug nimmt nichts (voll,
    # Ladetimer). Dagegen hilft kein enger Takt, nur gelegentlich nachfragen.
    wartezeit = MAX_START_RETRY_S if state.box_status == "SuspendedEV" else state.start_retry_s
    await apply_limit(now, amps)
    status = await charge_point.remote_start()
    state.last_adjust = now
    if status == "Accepted":
        # Angenommen heißt nicht geladen — bis die Box "Charging" meldet,
        # bleibt der Abstand stehen, danach setzt on_status alles zurück
        state.start_retry_at = now + wartezeit
        return
    state.start_attempts += 1
    delay = min(wartezeit * 2 ** (state.start_attempts - 1), MAX_START_RETRY_S)
    state.start_retry_at = now + delay
    log.warning("Start abgelehnt (%s), Box-Status %s — nächster Versuch in %.0fs",
                status, state.box_status, delay)
    if state.box_status == "Finishing" and not state.wake_tried:
        state.wake_tried = True
        await charge_point.wake_connector()


def warn_limit_ineffective(now: float):
    """Meldet, wenn bei voller Freigabe deutlich weniger fließt als erlaubt.

    Der Fall, der das nötig macht: die Box nimmt das Ladeprofil an, richtet
    sich aber nicht danach. Dagegen hilft kein Nachsetzen, nur Hinsehen — die
    Nacht zum 2026-07-20 fiel erst am Morgen auf. Ein Fahrzeug darf legitim
    weniger ziehen (einphasig, eigene Begrenzung), deshalb bleibt es bei einer
    Warnung pro Ladevorgang und greift nicht in die Regelung ein.
    """
    if state.limit_warned or state.charge_w_src not in ("gemessen", "Energie"):
        return
    if state.charge_w_seen is None or now - state.charge_w_seen > state.charge_w_max_age_s:
        return
    erlaubt_w = state.current_limit * VOLTAGE * PHASES
    if state.charge_w >= state.limit_warn_factor * erlaubt_w:
        return
    state.limit_warned = True
    log.warning("Wallbox lädt mit %.0f W, erlaubt sind %.1f A (%.0f W) — Limit "
                "wirkt nicht. In der Wallbox-App nachsehen.",
                state.charge_w, state.current_limit, erlaubt_w)


def reset_start_backoff():
    """Nach erfolgreichem Start oder weggefallenem Trigger wieder bei null."""
    state.start_retry_at = None
    state.start_attempts = 0
    state.wake_tried = False


def reset_charge_meter():
    """Zählerstände der Wallbox verwerfen — nach Ladeende sind sie wertlos."""
    state.limit_warned = False
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
    # Mehr als PV minus Netz minus Batterie kann nicht ins Auto gehen, denn das
    # Haus verbraucht nie negativ. Ohne diesen Deckel bliebe eine Ladung, die
    # gar nicht stattfindet (Auto voll, Box meldet trotzdem "Charging"),
    # unbemerkt: der geschätzte Wert bläht den Überschuss genau so weit auf,
    # dass der minpv-Timeout nie anläuft.
    if state.pv_w is not None and state.grid_w is not None:
        cap = state.pv_w - state.grid_w - (state.battery_w or 0)
        est = max(0.0, min(est, cap))
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

    @on("BootNotification")
    def on_boot(self, charge_point_vendor, charge_point_model, **kwargs):
        log.info("Wallbox gebootet: %s %s", charge_point_vendor, charge_point_model)
        # Was in der Box an Ladeprofil steht, weiß nach einem Boot niemand mehr.
        # Ohne diese Zeile hielte der Regler seinen alten Merker für die
        # Wahrheit und schickte nie wieder ein Limit — die Box lädt dann mit
        # ihrem eigenen Default weiter (siehe docs/issue_limit_to_6A.md).
        state.limit_known = False
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
            reset_start_backoff()
        elif status in ("Available", "Finishing", "SuspendedEVSE", "SuspendedEV", "Faulted"):
            # SuspendedEV = die Box gibt frei, das Fahrzeug nimmt nichts an;
            # in aller Regel ist es voll. Kein Grund für uns zu stoppen, aber
            # auch keiner, im Regeltakt neue Startversuche zu schicken.
            state.charging = False
            reset_charge_meter()
        save_session()
        return call_result.StatusNotification()

    @on("Authorize")
    def on_authorize(self, id_tag):
        return call_result.Authorize(id_tag_info={"status": AuthorizationStatus.accepted})

    @on("StartTransaction")
    def on_start_transaction(self, connector_id, id_tag, meter_start, **kwargs):
        state.transaction_id = 1
        state.charging = True
        log.info("Transaktion gestartet (meter %s Wh)", meter_start)
        save_session()
        return call_result.StartTransaction(
            transaction_id=state.transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on("StopTransaction")
    def on_stop_transaction(self, meter_stop, transaction_id, **kwargs):
        state.transaction_id = None
        state.charging = False
        reset_charge_meter()
        log.info("Transaktion beendet (meter %s Wh)", meter_stop)
        save_session()
        return call_result.StopTransaction()

    @on("MeterValues")
    def on_meter_values(self, connector_id, meter_value, **kwargs):
        state.last_box_seen = datetime.datetime.now().timestamp()
        # Die Box schickt die Transaktions-ID in jedem Paket mit. Für den
        # Normalfall belanglos, aber sie ist der zweite Weg zurück in eine
        # laufende Ladung, wenn die Sicherung fehlt oder zu alt war — ohne
        # sie bliebe der Regler bis zum Ladeende ohne Stopp-Möglichkeit.
        laufend = kwargs.get("transaction_id")
        if laufend is not None and state.transaction_id != laufend:
            log.info("Laufende Transaktion %s aus MeterValues übernommen", laufend)
            state.transaction_id = laufend
            state.charging = True
            save_session()
        now = asyncio.get_event_loop().time()
        power_wh = energy_wh = None
        for entry in meter_value:
            # sampled_value, nicht sampledValue: die ocpp-Bibliothek wandelt die
            # Nutzlast vor dem Handler rekursiv in snake_case um. Mit dem
            # camelCase-Schlüssel traf die Schleife nie zu — jede gemeldete
            # Ladeleistung wurde stillschweigend verworfen und der Regler
            # schätzte sie stattdessen aus dem Limit (im Mitschnitt vom
            # 25.07.2026: 1822 von 1823 Ladetakten mit charge_w_src
            # "geschätzt", kein einziger "gemessen").
            for sample in entry.get("sampled_value", []):
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
        # Läuft gerade eine Transaktion, geht das Limit als TxProfile mit ihrer
        # ID und höherem stack_level raus. Ein TxDefaultProfile ist laut OCPP
        # 1.6 zwar auch auf eine laufende Ladung anzuwenden, aber Boxen halten
        # sich daran unterschiedlich streng — manche übernehmen es erst zur
        # nächsten Transaktion. Genau so bliebe ein Nachtstart bei den 6 A des
        # PV-Betriebs hängen (docs/issue_limit_to_6A.md).
        if state.transaction_id is not None:
            profil = {
                "charging_profile_id": 2,
                "stack_level": 1,
                "transaction_id": state.transaction_id,
                "charging_profile_purpose": ChargingProfilePurposeType.tx_profile,
            }
        else:
            profil = {
                "charging_profile_id": 1,
                "stack_level": 0,
                "charging_profile_purpose": ChargingProfilePurposeType.tx_default_profile,
            }
        request = call.SetChargingProfile(
            connector_id=1,
            cs_charging_profiles={
                **profil,
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
            state.limit_known = True
            log.info("Limit gesetzt: %.1f A", amps)
        else:
            # Abgelehnt heißt: in der Box steht jetzt irgendetwas anderes.
            state.limit_known = False
            log.warning("Limit %.1f A abgelehnt: %s", amps, response)

    async def remote_start(self) -> str:
        response = await self.call(call.RemoteStartTransaction(id_tag="pvueb", connector_id=1))
        status = response.status if response else "keine Antwort"
        log.info("RemoteStart: %s", status)
        return status

    async def wake_connector(self):
        """Box aus "Finishing" holen: Verfügbarkeit kurz aus und wieder an.

        Nach einer beendeten Transaktion bleiben manche Boxen in Finishing
        hängen, bis das Kabel gezogen wird, und lehnen dort jeden RemoteStart
        ab. Der Availability-Zyklus setzt den Zustand des Ladepunkts zurück,
        ohne dass jemand zum Auto laufen muss.
        """
        for availability in ("Inoperative", "Operative"):
            try:
                response = await self.call(
                    call.ChangeAvailability(connector_id=1, type=availability)
                )
                log.info("ChangeAvailability %s: %s", availability,
                         response.status if response else "keine Antwort")
            except Exception as exc:  # noqa: BLE001
                log.warning("ChangeAvailability %s fehlgeschlagen: %s", availability, exc)
                return
            await asyncio.sleep(1)

    async def remote_stop(self):
        if state.transaction_id is None:
            return
        response = await self.call(call.RemoteStopTransaction(transaction_id=state.transaction_id))
        log.info("RemoteStop: %s", response.status if response else "keine Antwort")
        # Abgelehnt heißt hier: die Box kennt diese Transaktion nicht (mehr).
        # Meist stammt sie aus einer Sicherung und die Ladung endete während
        # des Ausfalls. Den Merker wegwerfen, sonst schickt der Regeltakt
        # denselben Stopp bis in alle Ewigkeit.
        if response is not None and response.status == "Rejected":
            log.info("Transaktion %s ist der Box unbekannt — Merker verworfen",
                     state.transaction_id)
            state.transaction_id = None
            state.charging = False
            save_session()


async def on_connect(websocket):
    global charge_point
    charge_point_id = websocket.request.path.strip("/") or "unknown"
    # Basic Auth nur, wenn konfiguriert — die Wallbox-App muss dasselbe
    # Passwort tragen, sonst kommt die Box nicht mehr herein
    if state.ocpp_user and not basic_auth_ok(
            websocket.request.headers.get("Authorization"),
            state.ocpp_user, state.ocpp_password):
        log.warning("OCPP-Verbindung von %r ohne gültige Anmeldung abgewiesen",
                    charge_point_id)
        await websocket.close(code=1008, reason="unauthorized")
        return
    log.info("Wallbox verbunden: %r", charge_point_id)
    cp = ChargePoint(charge_point_id, websocket)
    cp.ws = websocket            # für box_link_task, das die Leitung notfalls kappt
    charge_point = cp
    state.last_box_seen = datetime.datetime.now().timestamp()
    try:
        await cp.start()
    except websockets.exceptions.ConnectionClosed:
        log.warning("Wallbox-Verbindung getrennt")
    except Exception as exc:  # noqa: BLE001 — eine kaputte Verbindung ist kein Prozessende
        log.warning("Wallbox-Verbindung beendet: %s", exc)
    finally:
        # Nur aufräumen, wenn wir noch der aktive Zugang sind. Die Pulsar öffnet
        # die neue Verbindung, bevor die alte zumacht (28.07.2026: fünf Wechsel
        # in zwei Minuten). Ein pauschales charge_point = None löschte dann die
        # *neue* Verbindung, und der Regeltakt lief mit `charge_point is None`
        # blind weiter, während StatusNotifications weiter hereinkamen.
        if charge_point is cp:
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
                        if state.modbus_block:
                            # Ein Zeitpunkt für Batterie und Netz — sonst erbt der
                            # berechnete PV-Überschuss den Versatz beider Messungen
                            block = await client.read_holding_registers(
                                REG_BLOCK_START, count=REG_BLOCK_COUNT, device_id=1
                            )
                            if block.isError():
                                log.warning("Blockread %d+%d nicht möglich (%s) — lese einzeln",
                                            REG_BLOCK_START, REG_BLOCK_COUNT, block)
                                state.modbus_block = False
                            else:
                                registers = block.registers
                                offset = REG_GRID_POWER - REG_BLOCK_START
                                state.battery_w = decode_i32(registers[0:2])
                                state.grid_w = decode_i32(registers[offset:offset + 2])
                                state.last_huawei_seen = datetime.datetime.now().timestamp()
                            await asyncio.sleep(0.3)  # SDongle nicht hetzen
                        if not state.modbus_block:
                            result = await client.read_holding_registers(
                                REG_GRID_POWER, count=2, device_id=1
                            )
                            if not result.isError():
                                state.grid_w = decode_i32(result.registers)
                                state.last_huawei_seen = datetime.datetime.now().timestamp()
                            await asyncio.sleep(0.3)
                            batt_result = await client.read_holding_registers(
                                REG_BATTERY_POWER, count=2, device_id=1
                            )
                            if not batt_result.isError():
                                state.battery_w = decode_i32(batt_result.registers)
                            await asyncio.sleep(0.3)
                        pv_result = await client.read_holding_registers(
                            REG_PV_POWER, count=2, device_id=1
                        )
                        if not pv_result.isError():
                            state.pv_w = decode_i32(pv_result.registers)
                        await asyncio.sleep(0.3)
                        pv_dc_result = await client.read_holding_registers(
                            REG_PV_DC_POWER, count=2, device_id=1
                        )
                        if not pv_dc_result.isError():
                            state.pv_dc_w = decode_i32(pv_dc_result.registers)
                        await asyncio.sleep(0.3)
                        soc_result = await client.read_holding_registers(
                            REG_BATTERY_SOC, count=1, device_id=1
                        )
                        if not soc_result.isError():
                            state.soc = soc_result.registers[0] * 0.1
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
        state.pv_w = None
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


def perma_boost() -> float:
    """Dauer-Boost aus einer vollen Hausbatterie (W), 0 wenn nicht aktiv.

    An sonnigen Tagen steht die Batterie mittags auf 100 % und kann nichts mehr
    aufnehmen — jede weitere Kilowattstunde ginge für ein paar Cent ins Netz.
    Ab PVUEB_PERMA_BOOST_ON_SOC schiebt sie deshalb dauerhaft
    PVUEB_PERMA_BOOST_W ins Auto, bis sie auf PVUEB_PERMA_BOOST_OFF_SOC
    abgesunken ist. Die beiden Schwellen sind eine Hysterese: ohne sie würde
    der Boost an der oberen Grenze im Sekundentakt an- und ausgehen.

    Der Sonnen-Autodetect steckt im SOC selbst — eine volle Batterie am
    Nachmittag ist der Beweis für den sonnigen Tag, den die Prognose morgens
    nur behauptet. Nachgeladen wird sie im normalen Ablauf wieder: Auto und
    Box ziehen selten alles, und abends unter der Mindestladeleistung geht der
    Überschuss ohnehin wieder in die Batterie.

    Bewusst ohne Tagesbudget — die Spanne zwischen den Schwellen ist das
    Budget. Was hier fließt, wird beim Wolkenloch-Boost nicht angerechnet.
    """
    if state.perma_boost_w <= 0 or state.soc is None:
        state.perma_boost_aktiv = False
        return 0.0
    if state.perma_boost_aktiv:
        if state.soc <= state.perma_boost_off_soc:
            log.info("Dauer-Boost aus: SOC %.0f %% erreicht die Untergrenze %.0f %%",
                     state.soc, state.perma_boost_off_soc)
            state.perma_boost_aktiv = False
    elif state.soc >= state.perma_boost_on_soc:
        log.info("Dauer-Boost an: Batterie bei %.0f %% (ab %.0f %%) — %d W zusätzlich "
                 "ins Auto, bis %.0f %%", state.soc, state.perma_boost_on_soc,
                 state.perma_boost_w, state.perma_boost_off_soc)
        state.perma_boost_aktiv = True
    return state.perma_boost_w if state.perma_boost_aktiv else 0.0


def boost_allowance(now: float) -> float:
    """Wieviel Batterie-Entladung darf gerade ins Auto fließen (W).

    Tagesbudget PVUEB_BOOST_W × PVUEB_BOOST_H, Leistung gedeckelt auf
    PVUEB_BOOST_W, Stopp
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
        # Was der Dauer-Boost aus der Batterie zieht, gehört nicht in dieses
        # Budget — sonst wäre es binnen Stunden leer und die Wolkenlöcher
        # blieben ungefedert, obwohl der Dauer-Boost seine eigene Grenze hat.
        entladung = -state.battery_w - (state.perma_boost_w if state.perma_boost_aktiv else 0)
        drawn = min(max(0.0, entladung), state.boost_w)
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
    save_session()
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
    save_session()
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



def wallbox_snapshot(daten: dict) -> dict:
    """Die paar Felder aus ~200 Zeilen Cloud-Antwort, die uns angehen."""
    leistung_kw = daten.get("charging_power")
    config = daten.get("config_data") or {}
    return {
        # Die Cloud liefert kW, alles andere im Projekt rechnet in W
        "power_w": None if leistung_kw is None else round(float(leistung_kw) * 1000),
        "added_kwh": daten.get("added_energy"),
        "added_km": daten.get("added_range"),
        "charging_time_s": daten.get("charging_time"),
        "status_id": daten.get("status_id"),
        "status": WALLBOX_STATUS.get(daten.get("status_id"), "unbekannt"),
        # Grenze aus der Wallbox-App. Im OCPP-Betrieb gilt unser Limit, aber
        # fällt die Box je aus dem OCPP-Modus, deckelt dieser Wert die Ladung
        "app_max_a": config.get("max_charging_current"),
        "hw_max_a": config.get("max_available_current"),
        "firmware": (config.get("software") or {}).get("currentVersion"),
        "locked": config.get("locked"),
        "last_sync": daten.get("last_sync"),
        "last_sync_s": sync_alter(daten.get("last_sync")),
    }


def sync_alter(text: str | None) -> int | None:
    """Wie lange die Box zuletzt mit ihrer Cloud gesprochen hat, in Sekunden.

    Nicht zu verwechseln mit dem Alter *unseres* Abrufs: die Box kann per OCPP
    munter antworten und trotzdem seit Stunden nichts an die Cloud gemeldet
    haben. Dann steht in der UI ein Standbild, das frisch aussieht.

    Der Zeitstempel kommt ohne Zonenangabe, steht aber in UTC — gegen die
    Ortszeit gerechnet ergab das im Sommer konstant 2 h Alter (im Winter 1 h)
    und damit einen Dauer-Alarm, obwohl die Box im Sekundentakt meldete.
    Ergibt die Rechnung etwas Unplausibles, wird lieber nichts angezeigt als
    eine falsche Zahl.
    """
    if not text:
        return None
    try:
        gesehen = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None
    alter = (datetime.datetime.now(datetime.timezone.utc) - gesehen).total_seconds()
    # Ein paar Sekunden Vorlauf sind Uhrendrift zwischen Pi und Cloud, kein Fehler
    if -120 <= alter < 0:
        alter = 0.0
    return round(alter) if 0 <= alter <= 30 * 86400 else None


async def wallbox_cloud_task():
    """Holt die Ladeleistung aus der myWallbox-Cloud — als Referenz, nicht zum Regeln.

    Die Pulsar Plus kennt ihre Ladeleistung, meldet sie über OCPP aber nicht
    (kein Power.Active.Import). In der Hersteller-Cloud steht sie. Von dort
    geregelt wird trotzdem nicht: Die Werte werden alle 30 s aktualisiert,
    der Regler arbeitet mit 5 s, und ein Internetausfall dürfte niemals eine
    Ladung beeinflussen. Der Wert dient dazu, die Schätzung aus dem Limit zu
    überprüfen — die Abweichung landet im Status und im Mitschnitt.
    """
    anmeldung = base64.b64encode(
        f"{state.wallbox_user}:{state.wallbox_password}".encode()).decode()
    kopf = {"Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=utf-8"}
    token, token_bis = None, 0.0
    async with aiohttp.ClientSession() as sitzung:
        while True:
            try:
                now = asyncio.get_running_loop().time()
                if token is None or now >= token_bis:
                    async with sitzung.post(
                            "https://api.wall-box.com/auth/token/user",
                            headers={**kopf, "Authorization": f"Basic {anmeldung}"},
                            timeout=aiohttp.ClientTimeout(total=15)) as antwort:
                        antwort.raise_for_status()
                        token = (await antwort.json())["jwt"]
                    # Das Token lebt kurz; früh genug erneuern, statt auf 401 zu warten
                    token_bis = now + WALLBOX_TOKEN_TTL_S
                    log.info("myWallbox: angemeldet")
                async with sitzung.get(
                        f"https://api.wall-box.com/chargers/status/{state.wallbox_id}",
                        headers={**kopf, "Authorization": f"Bearer {token}"},
                        timeout=aiohttp.ClientTimeout(total=15)) as antwort:
                    if antwort.status in (401, 403):
                        token = None          # abgelaufen: nächste Runde neu anmelden
                        raise RuntimeError(f"Anmeldung abgelehnt (HTTP {antwort.status})")
                    antwort.raise_for_status()
                    state.wallbox = wallbox_snapshot(await antwort.json())
                state.wallbox_error = None
                state.last_wallbox_ok = datetime.datetime.now().timestamp()
            except Exception as exc:  # noqa: BLE001 — Cloud-Fehler dürfen den Regler nie stoppen
                state.wallbox_error = str(exc) or exc.__class__.__name__
                log.warning("myWallbox-Abruf fehlgeschlagen: %s", state.wallbox_error)
            await asyncio.sleep(state.wallbox_poll_s)


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
        save_session()   # sonst startet die Automatik nach einem Neustart erneut
    else:
        log.warning("NACHT: Start fehlgeschlagen (%s) — neuer Versuch im nächsten Takt", error)


def betriebsstufe() -> tuple[str, str]:
    """Was der Regler gerade noch kann — als Aussage, nicht als Nebenwirkung.

    Die Rangfolge der Funktionen und ihre Abhängigkeiten:

        Nachtfenster laden      braucht Wallbox
        PV-Überschussladen      braucht Wallbox + Wechselrichter
        Akku aus dem Netz       braucht Wechselrichter (+ Prognose)

    Ohne diese Zeile in der UI ist ein eingeschränkter Betrieb von einem
    vollständigen nicht zu unterscheiden — man sieht nur, dass nichts lädt,
    und rät.
    """
    # Kurz halten: die Zeile steht auf der Statusseite und muss dort in eine
    # Zeile passen. Was im Einzelnen fehlt, steht auf der Diagnoseseite.
    if charge_point is None:
        return "kein Laden", "Wallbox getrennt"
    if state.grid_w is None:
        return "eingeschränkt", "PV-Messung weg, Nacht lädt"
    return "voll", "Box und PV ok"


def leitung_tot(jetzt: float) -> bool:
    """Steht die OCPP-Verbindung noch, schweigt die Box aber?

    Der harmlose Fall ist der Abriss — den meldet der Handler und
    `charge_point` wird frei. Der gefährliche ist die halbtote Leitung: TCP
    steht, die Box antwortet nicht mehr. Dann bleibt `charge_point` gesetzt,
    der Regeltakt schickt hinein, und jeder Aufruf läuft 30 s in den
    OCPP-Timeout. Mit dem Wächter über dem Regeltakt hieße das: alle zwei
    Minuten ein Prozessneustart, im Kreis.

    Getrennt von box_link_task, damit die Bedingung prüfbar ist.
    """
    if charge_point is None or state.last_box_seen is None:
        return False
    # Die Box meldet sich per Heartbeat, MeterValues und StatusNotification.
    # Dreifaches Heartbeat-Intervall lässt Raum für einen verlorenen Takt,
    # die Untergrenze für sehr kurze Intervalle.
    grenze = max(BOX_SILENCE_MIN_S, 3 * state.heartbeat_s)
    return (jetzt - state.last_box_seen) > grenze


async def box_link_task():
    """Eine schweigende Wallbox-Verbindung fallen lassen, statt hineinzureden.

    Wir schließen nur; den Neuaufbau macht die Box von selbst. Dass sie das
    kann, ist belegt — am 28.07.2026 hat sie es fünfmal in zwei Minuten getan.
    """
    global charge_point
    while True:
        await asyncio.sleep(BOX_LINK_CHECK_S)
        jetzt = datetime.datetime.now().timestamp()
        if not leitung_tot(jetzt):
            continue
        cp = charge_point
        log.warning("Wallbox schweigt seit %.0f s bei stehender Verbindung — "
                    "Leitung wird gekappt, damit die Box neu aufbaut",
                    jetzt - state.last_box_seen)
        # Zuerst freigeben, dann schließen: bis der Handler in on_connect
        # aufräumt, schickte der Regeltakt sonst weiter in die tote Leitung.
        if charge_point is cp:
            charge_point = None
        try:
            await cp.ws.close()
        except Exception as exc:  # noqa: BLE001 — das Kappen darf nie den Dienst stoppen
            log.warning("Schließen der Wallbox-Verbindung fehlgeschlagen: %s", exc)


async def control_task():
    """Der Regeltakt. Er darf unter keinen Umständen aufhören.

    Alles, was aus diesem Takt heraus die Wallbox anspricht, kann scheitern:
    eine abgerissene Verbindung wirft ConnectionClosed, eine stumme Box nach
    30 s TimeoutError. Vor dem 29.07.2026 flog beides ungebremst bis in das
    asyncio.gather in main() — der Regler war dann weg, während Web-UI,
    Modbus und OCPP-Server weiterliefen und alles gesund aussehen ließen
    (docs/issue_nightly_load_did_not_work.md). Deshalb: ein einzelner Takt
    darf fehlschlagen, die Schleife nicht.

    Der Herzschlag steht bewusst vor allen Abbruchbedingungen. Eine getrennte
    Wallbox oder ein toter Modbus sind Betriebszustände, keine Regler-Fehler —
    der Wächter soll dabei nicht neu starten, sondern nur, wenn dieser Takt
    selbst nicht mehr läuft.
    """
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(state.poll_interval_s)
        state.last_tick = time.monotonic()
        try:
            await battery_night_check()
            # Ohne Wallbox lässt sich nichts steuern — das ist die einzige
            # Bedingung, die den ganzen Takt aussetzen darf. Der Wechselrichter
            # gehört ausdrücklich *nicht* hierher: das Nachtfenster braucht ihn
            # nicht, und bis 29.07.2026 stand er trotzdem hier. Ein
            # Modbus-Abbruch (der `state.grid_w = None` setzt) legte damit auch
            # das Nachtladen still, obwohl beides nichts miteinander zu tun hat.
            if charge_point is None:
                state.tick_error = None
                continue
            await control_step(loop.time())
            state.tick_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — ein Takt darf scheitern, der Regler nicht
            state.tick_error = f"{exc.__class__.__name__}: {exc}"
            log.exception("Regeltakt fehlgeschlagen (%s) — weiter im nächsten Takt",
                          state.tick_error)


async def control_step(now: float):
    """Ein Regeltakt. Getrennt von control_task, damit test_sim.py ihn mit
    simulierter Zeit und simulierter Wallbox durchspielen kann."""
    if not state.released:
        if state.charging:
            log.info("Freigabe fehlt — stoppe Ladung")
            await charge_point.remote_stop()
        state.surplus_since = state.deficit_since = state.minpv_low_since = None
        # Ohne Freigabe regeln wir nicht mehr, aber die Box lädt weiter, wenn
        # jemand sie per RFID oder Hersteller-App startet. Das gedrosselte
        # Limit aus dem letzten PV-Takt bliebe dabei stehen — deshalb hier
        # zurück auf Maximum, damit unbeaufsichtigtes Laden voll läuft.
        if limit_due(now, MAX_AMPS):
            await apply_limit(now, MAX_AMPS)
        return

    # Nachttarif-Fenster oder Modus "fast": volle Leistung
    if state.mode == "fast" or in_night_window():
        if state.charging:
            reset_start_backoff()
            if limit_due(now, MAX_AMPS):
                await apply_limit(now, MAX_AMPS)
            warn_limit_ineffective(now)
        else:
            await try_start(now, MAX_AMPS)
        return

    # Ab hier wird geregelt, und dafür braucht es die Messung. Ohne
    # Wechselrichter ist der Überschuss unbekannt: nicht null, sondern
    # unbekannt. Eine laufende Ladung deshalb abzubrechen wäre falsch — der
    # Modbus-Reconnect dauert 10 s —, also bleibt das zuletzt gesetzte Limit
    # stehen, bis wieder Zahlen kommen. Sichtbar wird das über betriebsstufe().
    if state.grid_w is None:
        return

    surplus = state.grid_w + charge_power(now)
    # Echter PV-Überschuss: Batterie-Ladung wäre für das Auto verfügbar,
    # Batterie-Entladung täuscht Überschuss nur vor (Netzpunkt bleibt ~0)
    pv_surplus = surplus + (state.battery_w or 0)
    pv_avg = pv_average(now, pv_surplus)
    # Reihenfolge zählt: der Dauer-Boost muss stehen, bevor das Budget des
    # Wolkenloch-Boosts gebucht wird — sonst zöge er es leer.
    perma = perma_boost()
    allowance = boost_allowance(now)   # bucht Verbrauch, darum immer aufrufen
    # Wolkenloch-Boost: nur bei laufender Ladung. Er hebt das Ziel nicht an,
    # sondern deckelt es weicher — die Batterie füllt Einbrüche unter dem
    # Mittelwert auf, statt die Ladung herunterzuregeln.
    bridge = allowance if state.charging else 0.0
    min_w = state.min_amps * VOLTAGE * PHASES
    # Der Dauer-Boost kommt oben drauf: er hebt das Ziel wirklich an, ohne
    # Rücksicht auf Schwellen. Er gilt auch vor dem Start — eine Batterie an
    # der oberen Schwelle kann ohnehin nichts mehr aufnehmen, und die Ladung
    # soll früher anlaufen, nicht der Überschuss ins Netz gehen.
    avail = min(pv_avg, pv_surplus + bridge) + perma
    # Starthilfe: fehlen zur Schwelle nur ein paar hundert Watt, legt eine
    # ausreichend volle Batterie sie drauf — sonst bliebe die Ladung wegen
    # einer Handvoll Watt aus. Anders als der Boost hebt sie das Ziel wirklich
    # an, aber nur bis zur Schwelle: liegt die PV darüber, kostet sie nichts.
    # Sie gilt auch bei laufender Ladung (dann bis zur Mindestleistung), sonst
    # ruderte der Regler direkt nach dem Start in die Stopp-Schwelle zurück.
    help_w = 0.0
    if (state.boost_start_w and allowance and state.soc is not None
            and state.soc >= state.boost_start_soc):
        if state.mode == "minpv":
            noetig = min_w if state.charging else state.minpv_start_factor * min_w
        else:
            noetig = MIN_AMPS * VOLTAGE * PHASES
        help_w = min(max(0.0, noetig - avail), state.boost_start_w)
        avail += help_w
    # Alle drei Beiträge kommen aus derselben Batterie, und die hat eine
    # Entladegrenze. Ohne diesen Deckel verspräche der Regler Leistung, die
    # der Wechselrichter nicht liefern kann — die Differenz käme aus dem Netz.
    # Betroffen ist vor allem der Dauer-Boost im Wolkenloch: dort schöpft der
    # Wolkenloch-Boost die Grenze schon aus, „oben drauf" gibt es dann nichts.
    avail = min(avail, pv_surplus + state.batt_max_w)
    boost = max(bridge, help_w) + perma  # nur noch für Log und Anzeige
    amps = target_amps(avail)
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
                if state.start_retry_at is None:
                    log.info("Überschuss %.0f W seit %ds%s — starte Ladung mit %.1f A",
                             surplus, state.start_delay_s,
                             " (davon %.0f W Starthilfe der Batterie)" % boost if boost else "",
                             amps)
                await try_start(now, amps)
        else:
            state.surplus_since = None
            reset_start_backoff()   # Trigger weg: nächster Anlauf beginnt von vorn
            # Ohne Ladeabsicht bleibt sonst der Mindeststrom der letzten Regelung
            # in der Box stehen — sie speichert das TxDefaultProfile und zeigt es
            # in der Hersteller-App als feste Grenze an. Wer dann per RFID oder
            # App lädt, hängt an 6 A fest. Der nächste geregelte Start setzt
            # ohnehin wieder den passenden Wert (try_start).
            if limit_due(now, MAX_AMPS):
                await apply_limit(now, MAX_AMPS)
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
            # Das Totband hält das Funken klein, darf aber die Auffrischung
            # nicht verschlucken — sonst stünde ein abgewiesenes oder von außen
            # überschriebenes Limit unbemerkt bis zum nächsten Wolkenzug.
            faellig = (abs(amps - state.current_limit) >= state.limit_deadband_a
                       or limit_due(now, amps))
            if faellig and now - state.last_adjust >= state.adjust_min_interval_s:
                log.info("Überschuss %.0f W — passe Limit an: %.1f → %.1f A",
                         surplus, state.current_limit, amps)
                await apply_limit(now, amps)
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
  .bar { height:1.6rem; background:#333; border-radius:.3rem; overflow:hidden;
         margin:.5rem 0; position:relative; }
  .bar > i { display:block; height:100%; background:#2e7d32; }
  .bar > b { position:absolute; inset:0; text-align:center; line-height:1.6rem;
             font-weight:normal; font-variant-numeric:tabular-nums; }
  .bar.stale > i { background:#555; }
  .note { color:#aaa; font-size:.85rem; padding:.4rem 0; }
  .note.err { color:#ef9a9a; }
</style></head><body>
<h1>PVueb – Überschussladen</h1>
<div class="dots"><span class="dot on"></span><span class="dot"></span><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
<div class="pages" id="pages">
<section class="page">
<div id="stat"></div>
<h2>Freigabe</h2>
<button id="frei" onclick="toggleRelease()">…</button>
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
<h2>Steuerung</h2>
<div id="dbgstat"></div>
</section>
<section class="page">
<h2>Diagnose</h2>
<div id="diagstat"></div>
<h2>Box-Heartbeat: <span id="hblabel">10</span> s</h2>
<div class="slider-row">
  <input type="range" id="heartbeat" min="5" max="120" step="5"
         oninput="document.getElementById('hblabel').textContent=this.value"
         onchange="setConfig({heartbeat_s: +this.value})">
</div>
</section>
<section class="page">
<h2>Wallbox-Cloud <span id="wheart"></span></h2>
<div id="wbstat"></div>
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
// Wie ago(), aber ohne Warnzeichen. Für Werte, die von Natur aus selten
// frisch sind — die Box spricht im OCPP-Betrieb nur bei Verbindungsereignissen
// mit ihrer Hersteller-Cloud, ein Alter von Minuten ist dort der Normalfall
// und kein Fehler.
function agoRuhig(sec) {
  if (sec === null || sec === undefined) return "noch nie";
  if (sec < 90) return "vor " + sec + " s";
  if (sec < 5400) return "vor " + Math.round(sec/60) + " min";
  return "vor " + Math.round(sec/3600) + " h";
}
function hhmm(minutes) {
  return String(Math.floor(minutes/60)).padStart(2,"0") + ":" + String(minutes%60).padStart(2,"0");
}
function renderWallbox() {
  // Seite 4: was die Box selbst über sich sagt (Hersteller-Cloud)
  const out = document.getElementById("wbstat");
  const heart = document.getElementById("wheart");
  const row = r => `<div class="row"><span>${r[0]}</span><span class="val">${r[1]}</span></div>`;
  if (!s.wallbox_enabled) {
    heart.textContent = "";
    out.innerHTML = `<div class="note">Abfrage aus – PVUEB_WALLBOX_USER in der .env setzen,
      um Ladeleistung und Sitzungsdaten aus der Hersteller-Cloud zu holen.</div>`;
    return;
  }
  heart.textContent = s.wallbox_seen_s === null ? "⚪"
    : (s.wallbox_seen_s < 180 ? "🟢" : "🟠");
  const w = s.wallbox_cloud || {};
  const rows = [
    ["Ladeleistung (gemessen)", w.power_w === null || w.power_w === undefined ? "–"
      : Math.round(w.power_w) + " W ≈ " + (w.power_w / WPA).toFixed(1) + " A"],
    ["Regler schätzt", !s.charging ? "– (lädt nicht)"
      : Math.round(s.charge_w) + " W" + (s.charge_w_abweichung === null ? ""
        : " (" + (s.charge_w_abweichung >= 0 ? "+" : "") + s.charge_w_abweichung + " W)")],
    ["Status der Box", (w.status || "–") + (w.status_id ? " (" + w.status_id + ")" : "")],
    ["Sitzung geladen", w.added_kwh === undefined || w.added_kwh === null ? "–"
      : w.added_kwh + " kWh" + (w.added_km ? " ≈ " + w.added_km + " km" : "")],
    ["Ladezeit gesamt", w.charging_time_s ? (w.charging_time_s / 3600).toFixed(1) + " h" : "–"],
    ["Grenze in der App", w.app_max_a === undefined || w.app_max_a === null ? "–"
      : w.app_max_a + " A von " + (w.hw_max_a ?? "?") + " A"
        + (w.hw_max_a && w.app_max_a < w.hw_max_a ? " ⚠ deckelt die Ladung" : "")],
    ["Firmware", w.firmware || "–"],
    // Zwei verschiedene Wege, die gern verwechselt werden: die Box meldet an
    // ihren Hersteller, wir fragen dort ab. Nur das zweite liegt bei uns.
    ["Box → Hersteller-Cloud", agoRuhig(w.last_sync_s)],
    ["PVueb → Cloud abgefragt", ago(s.wallbox_seen_s)],
  ];
  // Die Box kann per OCPP antworten und trotzdem seit Stunden nichts an ihre
  // Cloud gemeldet haben. Ohne diesen Hinweis sieht ein Standbild frisch aus.
  const alt = w.last_sync_s !== null && w.last_sync_s !== undefined && w.last_sync_s > 600;
  out.innerHTML = rows.map(row).join("")
    + (s.wallbox_error ? `<div class="note err">${s.wallbox_error}</div>` : "")
    + (alt ? `<div class="note">Die Box hat seit ${agoRuhig(w.last_sync_s)} nichts
        an ihren Hersteller gemeldet – die Werte oben sind ein Standbild von
        damals. Im OCPP-Betrieb ist das normal: die Box meldet dorthin
        offenbar nur bei Verbindungs- und Ladeereignissen. Was PVueb direkt
        über OCPP sieht, steht auf der Steuerungsseite.</div>` : "")
    + `<div class="note">Referenzmessung – der Regler nutzt diese Werte nicht.
       Er misst selbst über die MeterValues der Box.</div>`;
}

// Referenzmessung aus der Hersteller-Cloud; die Regelung nutzt sie nicht.
// Auf drei kurze Zeilen verteilt, damit keine über den Rand läuft.
function cloudLeistung() {
  if (s.wallbox_error) return "⚠ " + s.wallbox_error;
  const w = s.wallbox_cloud || {};
  if (w.power_w === null || w.power_w === undefined) return "–";
  const diff = s.charge_w_abweichung;
  return Math.round(w.power_w) + " W"
    + (diff === null ? "" : " (Regler " + (diff >= 0 ? "+" : "") + diff + " W)");
}

// Die Starthilfe der Batterie senkt den nötigen PV-Überschuss um ihren Betrag
function minpvHilfe() {
  return (s.boost_start_w && s.boost_wh && s.soc !== null
          && s.soc >= s.boost_start_soc && s.soc >= s.boost_min_soc
          && s.boost_used_wh < s.boost_wh) ? s.boost_start_w : 0;
}
// Zwei kurze Zeilen statt einer langen: was der Regler gerade tut und ab
// welchem Wert er umschaltet — der Beitrag der Batterie steht daneben.
function minpvSchwelle() {
  if (s.mode !== "minpv") return "–";
  const minW = s.min_amps * WPA;
  if (s.minpv_low_s !== null)
    return "⛅ Stopp in " + Math.max(0, s.minpv_timeout_s - s.minpv_low_s) + " s";
  if (s.charging) return "lädt (Stopp < " + Math.round(s.minpv_pause_factor * minW) + " W)";
  return "wartet (" + (Math.round(s.minpv_start_factor * minW) - minpvHilfe()) + " W)";
}
function minpvBoost() {
  if (s.mode !== "minpv") return "–";
  const minW = s.min_amps * WPA;
  if (s.minpv_low_s !== null)
    return "Erholung ab " + Math.round(s.minpv_resume_factor * minW) + " W";
  const hilfe = minpvHilfe();
  return hilfe ? "Starthilfe 🔋 (" + hilfe + " W)" : "keine Starthilfe";
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
  if (s.charging) return "bereit, max " + s.boost_w + " W – " + budget;
  // Vor dem Start zählt nur die kleinere Starthilfe
  if (!s.boost_start_w) return "bereit, max " + s.boost_w + " W – " + budget;
  if (s.soc === null || s.soc < s.boost_start_soc)
    return "bereit, max " + s.boost_w + " W – Starthilfe ab " + s.boost_start_soc + " % SOC";
  return "Starthilfe " + s.boost_start_w + " W bereit – " + budget;
}
function permaInfo() {
  // Dauer-Boost: läuft zwischen zwei SOC-Schwellen, ohne Tagesbudget
  if (!s.perma_boost_w) return "aus";
  const spanne = s.perma_boost_on_soc + " → " + s.perma_boost_off_soc + " %";
  if (s.perma_boost_aktiv)
    return "🔋→🚗 " + s.perma_boost_w + " W – läuft bis " + s.perma_boost_off_soc + " % SOC";
  if (s.soc === null) return "wartet – " + spanne;
  return "wartet – ab " + s.perma_boost_on_soc + " % (jetzt " + s.soc.toFixed(0) + " %)";
}
function gridShare() {
  if (s.grid_w === null) return "–";
  const imp = Math.max(0, -s.grid_w);
  if (imp < 1) return "0 W";
  const txt = Math.round(imp) + " W ≈ " + (imp / WPA).toFixed(1) + " A";
  if (s.charge_w < 1) return txt + " (Haus)";
  return txt + " – " + Math.round(100 * Math.min(imp, s.charge_w) / s.charge_w) + " % der Ladung";
}
function heart(seen_s, limit) {
  const ok = seen_s !== null && seen_s !== undefined && seen_s < limit;
  return ok ? '<span class="heart">❤️</span>' : '<span class="heart dead">💔</span>';
}
// Der einzige Wert, an dem hängt, ob überhaupt noch geregelt wird. Alles
// andere auf dieser Seite kann frisch aussehen, während der Regler steht.
function tickInfo() {
  if (s.tick_age_s === null || s.tick_age_s === undefined) return "💔 läuft nicht";
  const txt = heart(s.tick_age_s, s.tick_timeout_s) + " vor " + s.tick_age_s + " s";
  return s.tick_error ? txt + ' <span class="err">⚠ ' + s.tick_error + "</span>" : txt;
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
    ["Betrieb", ({"voll":"✅ ","eingeschränkt":"⚠ ","kein Laden":"⛔ "}[s.betrieb] || "")
      + s.betrieb_text],
  ];
  document.getElementById("stat").innerHTML = rows.map(row).join("");
  // Steuerungsseite: nur, was die Regelentscheidung erklärt — von der Messung
  // oben bis zu den Beiträgen, die den Start ermöglichen.
  const drows = [
    ["Box-Status (OCPP)", s.box_status],
    ["Limit", s.current_limit.toFixed(1) + " A"],
    ["PV vom Dach", s.pv_dc_w === null || s.pv_dc_w === undefined ? "–"
      : Math.round(s.pv_dc_w) + " W"],
    ["Wechselrichter raus (AC)", s.pv_w === null ? "–" : Math.round(s.pv_w) + " W"],
    ["Hausverbrauch", s.house_w === null ? "–" : Math.round(s.house_w) + " W"],
    ["Fürs Auto frei", s.pv_surplus_w === null ? "–"
      : Math.round(s.pv_surplus_w) + " W ≈ " + (s.pv_surplus_w / WPA).toFixed(1) + " A"],
    ["PV-Mittel " + (s.avg_active_s ? Math.round(s.avg_active_s/60) + " min" : "aus")
      + (s.charging ? "" : " (Start)"),
      s.pv_avg_w === null ? "–"
      : Math.round(s.pv_avg_w) + " W ≈ " + (s.pv_avg_w / WPA).toFixed(1) + " A"],
    ["minpv-Schwelle", minpvSchwelle()],
    ["minpv-Boost", minpvBoost()],
    ["Netzanteil", gridShare()],
    ["Batterie-Boost", boostInfo()],
    ["Dauer-Boost", permaInfo()],
    ...(s.wallbox_enabled ? [
      ["Wallbox Cloud: Leistung", cloudLeistung()],
      ["Wallbox Cloud: Status", (s.wallbox_cloud || {}).status || "–"],
      ["Wallbox Cloud: Update", agoRuhig(s.wallbox_seen_s)],
    ] : []),
  ];
  document.getElementById("dbgstat").innerHTML = drows.map(row).join("");
  // Diagnoseseite: lebt der Dienst noch, und welcher Stand läuft überhaupt
  const diagrows = [
    ["Regeltakt", tickInfo()],
    ["Heartbeat Box", ago(s.box_seen_s)],
    ["Heartbeat Huawei", ago(s.huawei_seen_s)],
    ["Betrieb", s.betrieb + " – " + s.betrieb_text],
    ["Version", `<a href="/info">${(s.version||{}).commit || "?"}</a>`],
    ["Sitzung beim Start", s.session_note || "–"],
  ];
  document.getElementById("diagstat").innerHTML = diagrows.map(row).join("");
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
  renderWallbox();
  for (const m of ["pv","minpv","fast"])
    document.getElementById("m_"+m).classList.toggle("on", s.mode === m);
  document.getElementById("minlabel").textContent = s.min_amps;
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


def house_power() -> float | None:
    """Hausverbrauch aus der Energiebilanz.

    Alles, was die Module liefern, geht in die Batterie, ins Netz, ins Auto
    oder ins Haus:

        Haus = PV(DC) − Batterieladung − Einspeisung − Auto

    Über die Dachleistung gerechnet, weil sie als einzige unabhängig von der
    Batterie ist. Fehlt sie, tritt der AC-Ausgang an ihre Stelle; der hat die
    Batterie schon verrechnet, weshalb sie dann nicht noch einmal abgezogen
    werden darf.
    """
    if state.grid_w is None:
        return None
    if state.pv_dc_w is not None:
        return state.pv_dc_w - (state.battery_w or 0) - state.grid_w - state.charge_w
    if state.pv_w is None:
        return None
    return state.pv_w - state.grid_w - state.charge_w


def status_dict() -> dict:
    """Alles, was UI und Mitschnitt brauchen — eine Quelle für beide."""
    return {
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
        "pv_w": state.pv_w,
        "pv_dc_w": state.pv_dc_w,
        # Was das Haus zieht. Aufgestellt über die Dachleistung, weil nur sie
        # von der Batterie unabhängig ist: was die Module liefern, geht in die
        # Batterie, ins Netz, ins Auto oder ins Haus. Ist 32064 nicht lesbar,
        # bleibt der AC-Ausgang als Näherung — der enthält die Batterie bereits,
        # deshalb dort ohne battery_w.
        "house_w": house_power(),
        "modbus_block": state.modbus_block,
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
        "boost_start_w": state.boost_start_w,
        "boost_start_soc": state.boost_start_soc,
        "perma_boost_w": state.perma_boost_w,
        "perma_boost_on_soc": state.perma_boost_on_soc,
        "perma_boost_off_soc": state.perma_boost_off_soc,
        "perma_boost_aktiv": state.perma_boost_aktiv,
        "minpv_timeout_s": state.minpv_timeout_s,
        "minpv_start_factor": state.minpv_start_factor,
        "minpv_pause_factor": state.minpv_pause_factor,
        "minpv_resume_factor": state.minpv_resume_factor,
        "current_limit": state.current_limit,
        # Trägt das Limit? Im Mitschnitt ist damit später nachweisbar, ob die
        # Box sich an das Ladeprofil gehalten hat (docs/issue_limit_to_6A.md).
        "limit_effective": not state.limit_warned,
        "charging": state.charging,
        "night": in_night_window(),
        "battery_grid_charge": state.battery_grid_charge,
        "box_connected": charge_point is not None,
        "box_status": state.box_status,
        "box_seen_s": None if state.last_box_seen is None
                      else round(datetime.datetime.now().timestamp() - state.last_box_seen),
        "huawei_seen_s": None if state.last_huawei_seen is None
                         else round(datetime.datetime.now().timestamp() - state.last_huawei_seen),
        "wallbox_enabled": bool(state.wallbox_user),
        "wallbox_cloud": state.wallbox,
        "wallbox_error": state.wallbox_error,
        "wallbox_seen_s": None if state.last_wallbox_ok is None
                          else round(datetime.datetime.now().timestamp() - state.last_wallbox_ok),
        # Wie weit liegt die geschätzte Ladeleistung neben der gemessenen?
        # Gerade 0 W bei laufender Ladung ist aussagekräftig — dann glaubt der
        # Regler zu laden, während die Box nichts abgibt
        "charge_w_abweichung": None if state.wallbox.get("power_w") is None
                                       or not state.charging
                               else round(state.charge_w - state.wallbox["power_w"]),
        # Alter des letzten Regeltakts. Die eine Zahl, an der ablesbar ist, ob
        # der Regler noch lebt — alles andere hier (Netz, SOC, Box-Status) kann
        # frisch aussehen, während niemand mehr regelt (docs/issue_nightly_load_did_not_work.md).
        "tick_age_s": None if not state.last_tick
                      else round(time.monotonic() - state.last_tick),
        "tick_timeout_s": state.watchdog_s,
        "tick_error": state.tick_error,
        "betrieb": betriebsstufe()[0],
        "betrieb_text": betriebsstufe()[1],
        "version": VERSION,
        "session_note": state.session_note,
        "uptime_s": round(datetime.datetime.now().timestamp() - state.started_at),
        # Meldeweg: dass er steht, sieht man sonst erst daran, dass eine
        # erwartete Nachricht ausbleibt — und das ist zu spät.
        "notify_enabled": bool(state.notify_url),
        "notify_queue": len(meldungen),
        "notify_sent": state.notify_sent,
        "notify_dropped": state.notify_dropped,
        "notify_error": state.notify_error,
    }


async def http_status(_request):
    return web.json_response(status_dict())


def laufzeit(sekunden: float) -> str:
    tage, rest = divmod(int(sekunden), 86400)
    stunden, rest = divmod(rest, 3600)
    minuten = rest // 60
    if tage:
        return f"{tage} d {stunden} h {minuten} min"
    return f"{stunden} h {minuten} min" if stunden else f"{minuten} min"


async def http_info(_request):
    """Was läuft hier gerade — Version, Commit, Sitzung.

    Eigene Seite statt einer Zeile im UI: Nach einem Update ist die erste
    Frage, ob der neue Stand überhaupt drin ist, und die zweite, ob die
    laufende Ladung den Neustart überstanden hat.
    """
    jetzt = datetime.datetime.now()
    zeilen = [
        ("Commit", VERSION.get("commit") or "unbekannt"),
        ("Beschreibung", VERSION.get("beschreibung") or "–"),
        ("Herkunft", VERSION.get("quelle")),
        ("Gebaut", VERSION.get("gebaut") or "–"),
        ("Gestartet", datetime.datetime.fromtimestamp(state.started_at).strftime("%d.%m.%Y %H:%M:%S")),
        ("Läuft seit", laufzeit(jetzt.timestamp() - state.started_at)),
        ("Sitzung beim Start", state.session_note),
        ("Sicherung jetzt", session_status()),
        ("Systemuhr", {True: "✅ NTP synchron", False: "⚠ frei laufend, kein NTP",
                       None: "unbekannt"}[uhr_synchron()]),
        ("Sicherungsdatei", session_path()),
        ("Ladung", "läuft" if state.charging else "aus"),
        ("Transaktion", state.transaction_id if state.transaction_id is not None else "–"),
        ("Modus", state.mode),
        ("Freigabe", "erteilt" if state.released else "gesperrt"),
        ("Python", sys.version.split()[0]),
    ]
    tabelle = "".join(f"<tr><td>{name}</td><td>{wert}</td></tr>" for name, wert in zeilen)
    return web.Response(content_type="text/html", text=f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PVueb — Info</title>
<style>
 body {{ font-family: system-ui, sans-serif; background:#111; color:#eee; margin:0; padding:16px; }}
 h1 {{ font-size:1.3rem; margin:0 0 12px; }}
 table {{ border-collapse:collapse; width:100%; max-width:640px; }}
 td {{ padding:7px 10px; border-bottom:1px solid #333; vertical-align:top; }}
 td:first-child {{ color:#9ab; white-space:nowrap; width:1%; }}
 td:last-child {{ font-family:ui-monospace, monospace; word-break:break-all; }}
 a {{ color:#7cf; display:inline-block; margin-top:16px; }}
</style></head><body>
<h1>PVueb — was hier läuft</h1>
<table>{tabelle}</table>
<a href="/">&larr; zurück zur Übersicht</a>
</body></html>""")


# Was je Abtastung mitgeschrieben wird. Die Schwellen und Faktoren stehen
# einmal je Datei in der Kopfzeile — sie ändern sich im Betrieb praktisch nie
# und würden den Mitschnitt sonst um ein Vielfaches aufblähen.
RECORD_FIELDS = (
    "grid_w", "battery_w", "pv_w", "pv_dc_w", "house_w", "soc",
    "charge_w", "charge_w_src", "current_limit", "limit_effective",
    "charging", "box_status",
    "surplus_w", "pv_surplus_w", "pv_avg_w", "avg_active_s", "minpv_low_s",
    "mode", "min_amps", "night", "boost_used_wh", "battery_grid_charge",
    # Referenzmessung aus der Cloud: erlaubt später, die Schätzung zu bewerten
    "wallbox_cloud", "charge_w_abweichung",
)
RECORD_NAME = "status-%s.jsonl"          # je Tag eine Datei
RECORD_GLOB = re.compile(r"^status-\d{4}-\d{2}-\d{2}\.jsonl$")


def prune_recordings():
    """Ringbuffer: alles löschen, was älter als record_keep_days ist.

    Es werden ausschließlich Dateien angefasst, die dem Namensmuster des
    Mitschnitts entsprechen — sonst räumt der Regler fremde Daten ab.
    """
    grenze = datetime.date.today() - datetime.timedelta(days=state.record_keep_days)
    for name in os.listdir(state.record_dir):
        if not RECORD_GLOB.match(name):
            continue
        try:
            tag = datetime.date.fromisoformat(name[len("status-"):-len(".jsonl")])
        except ValueError:
            continue
        if tag < grenze:
            os.remove(os.path.join(state.record_dir, name))
            log.info("Mitschnitt %s gelöscht (älter als %d Tage)", name, state.record_keep_days)


async def record_task():
    """Regelbetrieb mitschreiben — Rohmaterial für Testfälle in test_sim.py.

    Eine JSON-Zeile je Abtastung, eine Datei pro Tag, ältere Tage fallen
    hinten raus. Fehler dürfen den Regler nicht mitreißen: schlägt das
    Schreiben fehl, wird geloggt und weiter gemessen.
    curve_from_recording.py macht aus den Dateien PV-Kurven.
    """
    os.makedirs(state.record_dir, exist_ok=True)
    log.info("Mitschnitt aktiv: %s (alle %ds, %d Tage)", state.record_dir,
             state.record_interval_s, state.record_keep_days)
    day, fh = None, None
    try:
        while True:
            try:
                now = datetime.datetime.now()
                if now.date() != day:
                    if fh:
                        fh.close()
                    day = now.date()
                    path = os.path.join(state.record_dir, RECORD_NAME % day.isoformat())
                    neu = not os.path.exists(path)
                    fh = open(path, "a", buffering=1)
                    if neu:   # Kopfzeile: alles, was nicht je Takt gebraucht wird
                        fh.write(json.dumps({"t": now.isoformat(timespec="seconds"),
                                             "config": status_dict()}) + "\n")
                    prune_recordings()
                sample = {"t": now.isoformat(timespec="seconds")}
                voll = status_dict()
                sample.update({k: voll[k] for k in RECORD_FIELDS})
                fh.write(json.dumps(sample) + "\n")
            except Exception as exc:  # noqa: BLE001 — Mitschnitt darf nie den Regler stoppen
                log.warning("Mitschnitt fehlgeschlagen: %s", exc)
            await asyncio.sleep(state.record_interval_s)
    finally:
        if fh:
            fh.close()


async def http_mode(request):
    mode = request.match_info["mode"]
    if mode not in ("pv", "minpv", "fast"):
        raise web.HTTPBadRequest(text="mode muss pv|minpv|fast sein")
    state.mode = mode
    state.surplus_since = state.deficit_since = state.minpv_low_since = None
    log.info("Modus (Web): %s", mode)
    save_session()
    return web.json_response({"ok": True})


async def http_release(request):
    state.released = request.match_info["onoff"] == "on"
    log.info("Freigabe (Web): %s", "erteilt" if state.released else "zurückgenommen")
    save_session()
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
    # Im Web-UI geänderte Werte überleben so den nächsten Neustart
    write_dotenv("Änderung im Web-UI")
    save_session()
    return web.json_response({"ok": True})


def basic_auth_ok(header: str | None, user: str, password: str) -> bool:
    """Prüft einen Authorization-Header gegen Benutzer und Passwort.

    Vergleich läuft über compare_digest, damit die Antwortzeit nichts über
    den Treffer verrät.
    """
    if not header or not header.startswith("Basic "):
        return False
    try:
        entschluesselt = base64.b64decode(header[6:]).decode("utf-8")
        gesendet_user, _, gesendet_pw = entschluesselt.partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    return (secrets.compare_digest(gesendet_user, user)
            and secrets.compare_digest(gesendet_pw, password))


@web.middleware
async def auth_middleware(request, handler):
    """Basic Auth für die Web-UI, sofern PVUEB_WEB_USER gesetzt ist.

    Ohne Zugangsdaten steuert jeder im Netz die Wallbox und die Hausbatterie.
    Der Standard bleibt trotzdem offen, weil das Gerät im eigenen LAN steht
    und ein plötzlich verlangtes Passwort den Betrieb bricht — wer den Port
    weiter aufmacht, sollte die Variablen setzen.
    """
    if not state.web_user:
        return await handler(request)
    if basic_auth_ok(request.headers.get("Authorization"),
                     state.web_user, state.web_password):
        return await handler(request)
    return web.Response(status=401, text="Anmeldung erforderlich\n",
                        headers={"WWW-Authenticate": 'Basic realm="PVueb"'})


async def web_task(port: int):
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", http_index)
    app.router.add_get("/info", http_info)
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


ENV_PFAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, ".env")


def load_dotenv():
    """Minimaler .env-Lader (Projektverzeichnis), ohne Zusatz-Dependency."""
    if not os.path.exists(ENV_PFAD):
        return
    with open(ENV_PFAD) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def env_spec() -> list[tuple[str, list[tuple[str, object, str]]]]:
    """Alle Einstellungen mit aktuellem Wert und Erklärung, nach Themen sortiert.

    Grundlage für write_dotenv(): So steht in der .env immer der vollständige
    Satz, auch für Werte, die bisher nur als Default im Code existierten.
    """
    def hhmm(minuten: int) -> str:
        return f"{minuten // 60:02d}:{minuten % 60:02d}"

    return [
        ("Wechselrichter", [
            ("PVUEB_INVERTER_IP", os.environ.get("PVUEB_INVERTER_IP", ""),
             "IP des SDongle. Pflichtangabe, sonst startet der Regler nicht."),
        ]),
        ("Standort und Anlage (Sonnenprognose über forecast.solar)", [
            ("PVUEB_LAT", state.lat, "Breitengrad"),
            ("PVUEB_LON", state.lon, "Längengrad"),
            ("PVUEB_PV_KWP", state.pv_kwp, "Anlagenleistung in kWp"),
            ("PVUEB_PV_TILT", state.pv_tilt, "Modulneigung in Grad"),
            ("PVUEB_PV_AZIMUT", state.pv_azimut,
             "Ausrichtung: 0 = Süd, -90 = Ost, 90 = West"),
        ]),
        ("Nachttarif", [
            ("PVUEB_NIGHT_START", hhmm(state.night_start_min),
             "Beginn des Tariffensters, Format HH:MM"),
            ("PVUEB_NIGHT_END", hhmm(state.night_end_min),
             "Ende des Fensters. Über Mitternacht erlaubt (22:00 -> 06:00)."),
        ]),
        ("Regelzeiten", [
            ("PVUEB_POLL_INTERVAL_S", state.poll_interval_s, "Takt der Messung und Regelung"),
            ("PVUEB_ADJUST_MIN_INTERVAL_S", state.adjust_min_interval_s,
             "Mindestabstand zwischen zwei Limit-Änderungen"),
            ("PVUEB_START_DELAY_S", state.start_delay_s,
             "So lange muss der Überschuss reichen, bevor die Ladung startet"),
            ("PVUEB_STOP_DELAY_S", state.stop_delay_s,
             "So lange muss er fehlen, bevor gestoppt wird"),
            ("PVUEB_HEARTBEAT_S", state.heartbeat_s,
             "OCPP-Heartbeat der Wallbox, auch im Web-UI einstellbar"),
        ]),
        ("minpv: Startschwelle und Wolkenloch-Überbrückung", [
            ("PVUEB_MINPV_START_FACTOR", state.minpv_start_factor,
             "Start erst ab Faktor × Mindestleistung echtem Überschuss"),
            ("PVUEB_MINPV_PAUSE_FACTOR", state.minpv_pause_factor,
             "Darunter läuft der Timeout an"),
            ("PVUEB_MINPV_RESUME_FACTOR", state.minpv_resume_factor,
             "Darüber wird er verworfen. Bedingung: 0 < pause < resume <= start."),
            ("PVUEB_MINPV_TIMEOUT_MIN", round(state.minpv_timeout_s / 60, 2),
             "So lange werden Wolkenlöcher überbrückt, in Minuten"),
        ]),
        ("Mittelung und Auflösung des Ladelimits", [
            ("PVUEB_AVG_WINDOW_MIN", round(state.avg_window_s / 60, 2),
             "Mittelungsfenster beim Regeln, 0 = aus"),
            ("PVUEB_START_AVG_MIN", round(state.start_avg_window_s / 60, 2),
             "Kürzeres Fenster für die Startentscheidung"),
            ("PVUEB_LIMIT_STEP_A", state.limit_step_a, "Raster des Ladelimits in Ampere"),
            ("PVUEB_LIMIT_DEADBAND_A", state.limit_deadband_a,
             "Abweichung, ab der neu gesetzt wird"),
            ("PVUEB_LIMIT_REFRESH_S", state.limit_refresh_s,
             "Limit spätestens so oft wiederholen, auch unverändert"),
            ("PVUEB_LIMIT_WARN_FACTOR", state.limit_warn_factor,
             "Unter diesem Anteil des erlaubten Stroms gilt das Limit als wirkungslos"),
        ]),
        ("Messwerte der Wallbox", [
            ("PVUEB_CHARGE_W_MAX_AGE_S", state.charge_w_max_age_s,
             "Ältere Leistungsmeldung gilt als tot; dann wird aus dem Limit geschätzt"),
            ("PVUEB_START_RETRY_S", state.start_retry_s,
             "Grundabstand zwischen Startversuchen, verdoppelt sich bei Ablehnung"),
        ]),
        ("Batterie-Boost: Wolkenlöcher und Ladestart", [
            ("PVUEB_BOOST_W", state.boost_w, "Leistung, die die Hausbatterie nachschiebt"),
            ("PVUEB_BOOST_H", round(state.boost_wh / state.boost_w, 2) if state.boost_w else 0,
             "Tagesbudget in Stunden dieser Leistung, 0 = Boost aus"),
            ("PVUEB_BOOST_MIN_SOC", state.boost_min_soc, "Darunter kein Boost mehr"),
            ("PVUEB_BOOST_START_W", state.boost_start_w,
             "So viel an der Startschwelle fehlende Leistung füllt die Batterie auf, 0 = aus"),
            ("PVUEB_BOOST_START_SOC", state.boost_start_soc,
             "Starthilfe erst ab diesem SOC, muss >= PVUEB_BOOST_MIN_SOC sein"),
            ("PVUEB_BATT_MAX_W", state.batt_max_w,
             "Entladegrenze der Hausbatterie — deckelt alle Boosts zusammen"),
            ("PVUEB_PERMA_BOOST_W", state.perma_boost_w,
             "Dauer-Boost aus voller Batterie, 0 = aus"),
            ("PVUEB_PERMA_BOOST_ON_SOC", state.perma_boost_on_soc,
             "Ab diesem SOC springt der Dauer-Boost an"),
            ("PVUEB_PERMA_BOOST_OFF_SOC", state.perma_boost_off_soc,
             "Bis hierher darf er die Batterie leeren, muss unter ON_SOC liegen"),
        ]),
        ("Batterie-Netzladung im Nachtfenster", [
            ("PVUEB_BATT_LOW_SOC", state.batt_low_soc, "Automatik startet unter diesem SOC"),
            ("PVUEB_BATT_TARGET_SOC", state.batt_target_soc, "Ziel-SOC, dort stoppt der Wechselrichter"),
            ("PVUEB_BATT_CHARGE_W", state.batt_charge_w, "Ladeleistung aus dem Netz"),
            ("PVUEB_FORECAST_MIN_KWH", state.forecast_min_kwh,
             "Nur laden, wenn die Prognose für morgen darunter liegt"),
        ]),
        ("Mitschnitt für Testfälle", [
            ("PVUEB_SESSION_FILE", state.session_file,
             "Ablage der Sitzungssicherung, leer = Mitschnitt-Ordner bzw. neben dem Skript"),
            ("PVUEB_RECORD_DIR", state.record_dir, "Zielordner, leer = kein Mitschnitt"),
            ("PVUEB_RECORD_INTERVAL_S", state.record_interval_s, "Abstand der Zeilen"),
            ("PVUEB_RECORD_KEEP_DAYS", state.record_keep_days,
             "Ringbuffer: ältere Tage werden gelöscht"),
        ]),
        ("Anmeldung (leer = offen, wie bisher)", [
            ("PVUEB_WEB_USER", state.web_user, "Basic Auth für die Web-UI"),
            ("PVUEB_WEB_PASSWORD", state.web_password, ""),
            ("PVUEB_OCPP_USER", state.ocpp_user,
             "Basic Auth für OCPP. Erst in der Wallbox-App eintragen, dann hier!"),
            ("PVUEB_OCPP_PASSWORD", state.ocpp_password, ""),
        ]),
        ("Ladeleistung als Referenz aus der myWallbox-Cloud", [
            ("PVUEB_WALLBOX_USER", state.wallbox_user,
             "Konto der Wallbox-App, leer = Abfrage aus"),
            ("PVUEB_WALLBOX_PASSWORD", state.wallbox_password, ""),
            ("PVUEB_WALLBOX_ID", state.wallbox_id, "Nummer der Box (App oder my.wall-box.com)"),
            ("PVUEB_WALLBOX_POLL_S", state.wallbox_poll_s,
             "Abrufabstand; die Cloud aktualisiert alle 30 s"),
        ]),
        ("Betriebssicherheit", [
            ("PVUEB_WATCHDOG_S", state.watchdog_s,
             "Bleibt der Regeltakt so lange aus, endet der Prozess und Docker startet neu"),
        ]),
        ("Benachrichtigung", [
            ("PVUEB_NOTIFY_URL", state.notify_url,
             "Meldestelle, leer = aus. PVueb kennt keinen Messenger — es schickt "
             "ein JSON dorthin und vergisst es. Ziel ist myhome-messenger "
             "(http://myhome-messenger:8090/notify), ntfy tut es genauso"),
            ("PVUEB_NOTIFY_TIMEOUT_S", state.notify_timeout_s,
             "Zeitgrenze je Zustellversuch. Der Regeltakt wartet nie darauf"),
            ("PVUEB_MELDE_OFFLINE_S", state.melde_offline_s,
             "So lange dürfen Wechselrichter oder Wallbox weg sein, bevor ein "
             "Alarm rausgeht. Gilt auch als Schonzeit nach dem Start"),
        ]),
    ]


def write_dotenv(grund: str = "Start"):
    """Die .env vollständig zurückschreiben: jeder Wert, jeder mit Erklärung.

    Damit steht dort immer der komplette Satz — auch Einstellungen, die bisher
    nur als Default im Code existierten oder über das Web-UI geändert wurden.

    Die Datei enthält Zugangsdaten, deshalb mit Vorsicht: Es wird erst eine
    Sicherung angelegt, dann in eine temporäre Datei geschrieben und diese
    per Umbenennen an ihren Platz geschoben. Bricht etwas ab, bleibt entweder
    die alte oder die neue Datei stehen, nie eine halbe. Unbekannte Zeilen
    (eigene Variablen) werden am Ende angehängt, statt sie zu verlieren.
    """
    if not os.path.exists(ENV_PFAD):
        # Im Container kommen die Werte über env_file aus der Umgebung, die
        # Datei selbst liegt auf dem Host. Dort eine neue anzulegen, brächte
        # nur eine Karteileiche, die der nächste Neustart wegwirft.
        log.debug(".env nicht gefunden (%s) — nichts zurückzuschreiben", ENV_PFAD)
        return

    bekannte = {name for _, eintraege in env_spec() for name, _, _ in eintraege}
    fremd: list[str] = []
    with open(ENV_PFAD, encoding="utf-8") as fh:
        for zeile in fh:
            blank = zeile.strip()
            if (blank and not blank.startswith("#") and "=" in blank
                    and blank.partition("=")[0].strip() not in bekannte):
                fremd.append(blank)

    zeilen = [
        "# PVueb-Konfiguration — beim Start automatisch vervollständigt.",
        f"# Zuletzt geschrieben: {datetime.datetime.now():%d.%m.%Y %H:%M} ({grund}).",
        "# Änderungen bleiben erhalten; fehlende Werte werden mit dem ergänzt,",
        "# was gerade wirksam ist. Diese Datei enthält Zugangsdaten und gehört",
        "# nicht ins Repository (.gitignore).",
    ]
    for gruppe, eintraege in env_spec():
        zeilen += ["", f"# --- {gruppe} " + "-" * max(0, 60 - len(gruppe))]
        for name, wert, kommentar in eintraege:
            if kommentar:
                zeilen.append(f"# {kommentar}")
            zeilen.append(f"{name}={'' if wert is None else wert}")
    if fremd:
        zeilen += ["", "# --- Eigene Einträge (von PVueb nicht verwendet) " + "-" * 14]
        zeilen += fremd

    inhalt = "\n".join(zeilen) + "\n"
    temp = ENV_PFAD + ".tmp"
    try:
        shutil.copy2(ENV_PFAD, ENV_PFAD + ".bak")
        with open(temp, "w", encoding="utf-8") as fh:
            fh.write(inhalt)
        try:
            os.replace(temp, ENV_PFAD)      # atomar: nie eine halbe Datei
        except OSError:
            # Als einzelne Datei eingebundenes Volume (Docker) lässt sich nicht
            # ersetzen, nur beschreiben. Weniger sicher, aber die Sicherung von
            # eben liegt daneben.
            with open(ENV_PFAD, "w", encoding="utf-8") as fh:
                fh.write(inhalt)
            os.unlink(temp)
        log.info(".env vervollständigt (%d Einstellungen, Sicherung in .env.bak)",
                 len(bekannte))
    except OSError as exc:
        # Nur-lesend eingebundene Konfiguration ist ein legitimer Betriebsfall
        log.warning(".env konnte nicht geschrieben werden: %s", exc)
        if os.path.exists(temp):
            try:
                os.unlink(temp)
            except OSError:
                pass


async def bewacht(name: str, factory):
    """Eine Daueraufgabe so führen, dass ihr Tod niemanden mitreißt.

    Vorher hingen alle Aufgaben nackt in einem asyncio.gather: die erste, die
    eine Ausnahme warf, beendete main() und riss die übrigen mit. Ein Fehler
    im Mitschnitt hätte so den Regler gestoppt — und ein Fehler im Regler hat
    genau das getan (docs/issue_nightly_load_did_not_work.md).

    factory ist bewusst eine Funktion, kein fertiger Coroutine: ein Coroutine
    lässt sich nur einmal starten, für den Neuanlauf brauchen wir einen neuen.
    """
    while True:
        try:
            await factory()
            log.warning("Aufgabe %s ist beendet — Neustart in %d s", name, TASK_RESTART_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — eine Aufgabe darf sterben, der Dienst nicht
            log.exception("Aufgabe %s abgestürzt — Neustart in %d s", name, TASK_RESTART_S)
        await asyncio.sleep(TASK_RESTART_S)


def takt_tot(jetzt: float) -> bool:
    """Ist der Herzschlag des Regeltakts überfällig?

    Getrennt von watchdog(), weil sich die Bedingung so prüfen lässt, ohne
    einen Testlauf abzuschießen (poc/test_robust.py). Vor dem ersten Takt
    (last_tick == 0) gilt nichts als tot — sonst schlüge der Wächter im
    Anlauf zu, bevor überhaupt etwas laufen konnte.
    """
    return bool(state.last_tick) and (jetzt - state.last_tick) > state.watchdog_s


def watchdog():
    """Letzte Rückfallebene: schweigt der Regeltakt, endet der Prozess.

    Bewusst ein Thread und kein asyncio-Task. Bricht main() ab, cancelt
    asyncio.run alle Aufgaben — ein Wächter im selben gather stürbe genau in
    dem Moment mit, in dem er gebraucht wird. Ein Thread läuft weiter, auch
    wenn der Event-Loop steht.

    Und os._exit statt sys.exit: am 28.07.2026 hing der Interpreter-Shutdown
    selbst fest, weil ein blockierender Fremd-Thread im Default-Executor auf
    eine Cloud-Antwort wartete, die nie kam. Der Prozess lief danach 17 Stunden
    scheintot weiter — Web-UI bedienbar, Messwerte frisch, kein Regeln.
    os._exit umgeht jeden Aufräumpfad, der ebenfalls hängen könnte; alles, was
    einen Neustart überleben muss, steht ohnehin schon in der Sitzungsdatei.
    """
    while True:
        time.sleep(WATCHDOG_CHECK_S)
        if takt_tot(time.monotonic()):
            log.critical("Wächter: kein Regeltakt seit %.0f s (Grenze %d s) — "
                         "Prozess wird beendet, damit er neu starten kann",
                         time.monotonic() - state.last_tick, state.watchdog_s)
            logging.shutdown()                   # Meldung noch herausschreiben
            os._exit(1)


# ----------------------------------------------------------------- Meldestelle

# Die Warteschlange. Der Regeltakt legt hinein und läuft weiter — kein await
# auf den Versand, keine Wiederholung im Regelpfad. Ein toter Messenger darf
# niemals eine Ladung beeinflussen, dieselbe Regel wie für die myWallbox-Cloud.
meldungen: collections.deque = collections.deque(maxlen=NOTIFY_QUEUE_MAX)

# OCPP-Zustände, in denen ein Fahrzeug steckt. „Available" heißt frei, alles
# andere heißt: da hängt ein Kabel dran.
ANGESTECKT = ("Preparing", "Charging", "SuspendedEV", "SuspendedEVSE", "Finishing")


def melden(thema: str, text: str, stufe: str = "info",
           schluessel: str = "", sperre_s: float = MELDE_SPERRE_S):
    """Eine Meldung einreihen. Kehrt sofort zurück und wirft nie.

    Entprellt wird beim Empfänger — deshalb reisen schluessel und
    wiederholsperre_s nur mit. Sonst müsste jedes meldende Projekt dieselbe
    Logik nachbauen.
    """
    try:
        if len(meldungen) == NOTIFY_QUEUE_MAX:
            state.notify_dropped += 1      # deque wirft gleich das Älteste weg
        meldungen.append({
            "nachricht": {
                "quelle": "pvueb", "thema": thema, "text": text, "stufe": stufe,
                "schluessel": schluessel or f"{thema}-{stufe}",
                "wiederholsperre_s": sperre_s,
            },
            "erzeugt": datetime.datetime.now().timestamp(),
            "naechster": 0.0, "versuche": 0,
        })
    except Exception:  # noqa: BLE001 — eine Meldung darf nichts kaputt machen
        log.exception("Meldung ließ sich nicht einreihen")


def melde_lage(jetzt: float) -> dict:
    """Die Tatsachen, aus denen die Regeln ihre Schlüsse ziehen.

    Getrennt von den Regeln, damit test_melden.py sie erfinden kann, ohne
    Wallbox, Wechselrichter und Uhr.
    """
    return {
        "box_verbunden": charge_point is not None,
        "box_status": state.box_status,
        "laedt": state.charging,
        "nacht": in_night_window(),
        "akku_netzladung": state.battery_grid_charge,
        "wr_alter_s": None if state.last_huawei_seen is None
                      else jetzt - state.last_huawei_seen,
    }


class Melder:
    """Flanken erkennen und daraus Meldungen machen.

    Bewusst ein Beobachter neben dem Regler und kein Aufruf im Regelpfad: die
    Regeln lesen nur, können also nichts stoppen, verzögern oder abbrechen.
    Und sie stehen an einer Stelle, statt über zehn Handler verstreut.
    """

    def __init__(self, grenze_s: float, start: float):
        self.grenze_s = grenze_s
        self.start = start
        self.erste = True
        self.box_offline_seit: float | None = None
        self.box_gemeldet = False
        self.wr_offline_seit: float | None = None
        self.wr_gemeldet = False
        self.angesteckt = False
        self.laedt = False
        self.lud_nachts = False
        self.akku_lud = False

    def _uebernehmen(self, lage: dict):
        self.angesteckt = lage["box_status"] in ANGESTECKT
        self.laedt = lage["laedt"]
        self.lud_nachts = lage["nacht"]
        self.akku_lud = lage["akku_netzladung"]

    def pruefen(self, lage: dict, jetzt: float) -> list[dict]:
        # Erster Durchgang: nur merken. Sonst meldete jeder Neustart „Auto
        # angesteckt" und „lädt" für einen Zustand, der schon vorher galt.
        if self.erste:
            self.erste = False
            self._uebernehmen(lage)
            return []
        return self._offline(lage, jetzt) + self._flanken(lage)

    # -- Alarme: etwas ist weg ------------------------------------------

    def _offline(self, lage: dict, jetzt: float) -> list[dict]:
        # Anlaufschonzeit: unmittelbar nach dem Start ist noch nichts
        # verbunden. Ohne diese Zeile meldete jeder Neustart beide Geräte als
        # ausgefallen, und zwar bevor sie überhaupt eine Chance hatten.
        if jetzt - self.start < self.grenze_s:
            return []

        raus = []
        wr_weg = lage["wr_alter_s"] is None or lage["wr_alter_s"] > self.grenze_s
        raus += self._geraet(
            "wechselrichter", wr_weg, jetzt, "wr",
            "Wechselrichter antwortet nicht mehr (Modbus). Ohne ihn ist der "
            "PV-Überschuss unbekannt — Nachtladen und Akkusteuerung laufen "
            "weiter, Überschussladen nicht.")
        raus += self._geraet(
            "wallbox", not lage["box_verbunden"], jetzt, "box",
            "Wallbox ist nicht mehr verbunden (OCPP). Es kann nicht geladen "
            "werden, auch nicht im Nachtfenster.")
        return raus

    def _geraet(self, thema: str, weg: bool, jetzt: float,
                merker: str, was: str) -> list[dict]:
        seit = getattr(self, f"{merker}_offline_seit")
        gemeldet = getattr(self, f"{merker}_gemeldet")

        if weg:
            if seit is None:
                setattr(self, f"{merker}_offline_seit", jetzt)
                return []
            if not gemeldet and jetzt - seit >= self.grenze_s:
                setattr(self, f"{merker}_gemeldet", True)
                return [{"thema": thema, "stufe": "alarm",
                         "text": f"{was} Seit {(jetzt - seit) / 60:.0f} min."}]
            return []

        setattr(self, f"{merker}_offline_seit", None)
        if gemeldet:
            setattr(self, f"{merker}_gemeldet", False)
            # Die Entwarnung ist kein Beiwerk: ohne sie steht ein Alarm im
            # Chat, und man weiß bis zum nächsten Blick ins UI nicht, ob er
            # noch gilt.
            return [{"thema": thema, "stufe": "info",
                     "text": f"{thema.capitalize()} ist wieder da.",
                     "schluessel": f"{thema}-zurueck", "sperre_s": 0}]
        return []

    # -- Info: etwas hat sich geändert -----------------------------------

    def _flanken(self, lage: dict) -> list[dict]:
        raus = []

        angesteckt = lage["box_status"] in ANGESTECKT
        if angesteckt != self.angesteckt:
            self.angesteckt = angesteckt
            raus.append({
                "thema": "fahrzeug", "stufe": "info", "sperre_s": 0,
                "schluessel": f"fahrzeug-{'an' if angesteckt else 'ab'}",
                "text": ("Fahrzeug angesteckt." if angesteckt
                         else "Fahrzeug abgesteckt.")})

        if lage["laedt"] != self.laedt:
            if lage["laedt"]:
                # Die Art der Ladung merken: eine Nachtladung, die um 08:05
                # endet, ist immer noch eine Nachtladung. Würde beim Stopp das
                # aktuelle Fenster gelesen, hieße sie dort „PV-Laden beendet".
                self.lud_nachts = lage["nacht"]
            art = "Nachtladen" if self.lud_nachts else "PV-Laden"
            thema = "nachtladung" if self.lud_nachts else "pvladung"
            zustand = "gestartet" if lage["laedt"] else "beendet"
            self.laedt = lage["laedt"]
            raus.append({"thema": thema, "stufe": "info", "sperre_s": 0,
                         "schluessel": f"{thema}-{zustand}",
                         "text": f"{art} {zustand}."})

        if lage["akku_netzladung"] != self.akku_lud:
            self.akku_lud = lage["akku_netzladung"]
            raus.append({
                "thema": "hausakku", "stufe": "info", "sperre_s": 0,
                "schluessel": f"hausakku-{'an' if self.akku_lud else 'aus'}",
                "text": ("Hausakku wird aus dem Netz geladen."
                         if self.akku_lud
                         else "Netzladung des Hausakkus beendet.")})

        return raus


async def melde_task():
    """Die Regeln im eigenen Takt prüfen, getrennt vom Regeltakt."""
    melder = Melder(state.melde_offline_s, datetime.datetime.now().timestamp())
    while True:
        jetzt = datetime.datetime.now().timestamp()
        for meldung in melder.pruefen(melde_lage(jetzt), jetzt):
            melden(**meldung)
        await asyncio.sleep(MELDE_CHECK_S)


async def notify_zustellen(sitzung, jetzt: float):
    """Die Warteschlange leeren. Wirft nie — Fehler landen im Log und im UI.

    Der Kopf blockiert bewusst den Rest: die Reihenfolge der Meldungen ist Teil
    ihrer Aussage („weg" vor „wieder da").
    """
    while meldungen:
        eintrag = meldungen[0]
        if jetzt - eintrag["erzeugt"] > NOTIFY_MAX_AGE_S:
            meldungen.popleft()
            state.notify_dropped += 1
            continue
        if eintrag["naechster"] > jetzt:
            return
        try:
            async with sitzung.post(
                    state.notify_url, json=eintrag["nachricht"],
                    timeout=aiohttp.ClientTimeout(total=state.notify_timeout_s)) as antwort:
                if antwort.status >= 400:
                    raise RuntimeError(f"HTTP {antwort.status}")
        except Exception as fehler:  # noqa: BLE001 — nie ein Fehler nach oben
            eintrag["versuche"] += 1
            abstand = min(NOTIFY_RETRY_MAX_S,
                          NOTIFY_RETRY_MIN_S * 2 ** (eintrag["versuche"] - 1))
            eintrag["naechster"] = jetzt + abstand
            state.notify_error = str(fehler)[:200]
            log.warning("Meldung nicht zugestellt (%s) — nächster Versuch in %d s",
                        fehler, abstand)
            return
        meldungen.popleft()
        state.notify_sent += 1
        state.notify_error = None


async def notify_task():
    async with aiohttp.ClientSession() as sitzung:
        while True:
            await notify_zustellen(sitzung, datetime.datetime.now().timestamp())
            await asyncio.sleep(NOTIFY_SEND_CHECK_S)


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
    state.limit_warn_factor = float(os.environ.get("PVUEB_LIMIT_WARN_FACTOR", "0.6"))
    if not 0 <= state.limit_warn_factor < 1:
        sys.exit("PVUEB_LIMIT_WARN_FACTOR muss in [0, 1) liegen")
    state.avg_window_s = int(float(os.environ.get("PVUEB_AVG_WINDOW_MIN", "10")) * 60)
    state.start_avg_window_s = int(float(os.environ.get("PVUEB_START_AVG_MIN", "2")) * 60)
    if state.avg_window_s < 0 or state.start_avg_window_s < 0:
        sys.exit("PVUEB_AVG_WINDOW_MIN und PVUEB_START_AVG_MIN müssen >= 0 sein")
    state.boost_w = int(os.environ.get("PVUEB_BOOST_W", "2500"))
    state.boost_wh = int(float(os.environ.get("PVUEB_BOOST_H", "2")) * state.boost_w)
    state.boost_min_soc = float(os.environ.get("PVUEB_BOOST_MIN_SOC", "30"))
    if state.boost_w < 0 or state.boost_wh < 0 or not 0 <= state.boost_min_soc <= 100:
        sys.exit("PVUEB_BOOST_W/_H müssen >= 0 sein, PVUEB_BOOST_MIN_SOC 0–100")
    state.batt_max_w = int(os.environ.get("PVUEB_BATT_MAX_W", "2500"))
    if state.batt_max_w < 0:
        sys.exit("PVUEB_BATT_MAX_W muss >= 0 sein")
    state.perma_boost_w = int(os.environ.get("PVUEB_PERMA_BOOST_W", "1000"))
    state.perma_boost_on_soc = float(os.environ.get("PVUEB_PERMA_BOOST_ON_SOC", "90"))
    state.perma_boost_off_soc = float(os.environ.get("PVUEB_PERMA_BOOST_OFF_SOC", "50"))
    if state.perma_boost_w < 0 or not 0 <= state.perma_boost_off_soc <= 100 \
            or not 0 <= state.perma_boost_on_soc <= 100:
        sys.exit("PVUEB_PERMA_BOOST_W muss >= 0 sein, die SOC-Schwellen 0–100")
    # Ohne Abstand wäre es keine Hysterese, sondern ein Flattern im Regeltakt
    if state.perma_boost_w and state.perma_boost_off_soc >= state.perma_boost_on_soc:
        sys.exit("PVUEB_PERMA_BOOST_OFF_SOC muss unter PVUEB_PERMA_BOOST_ON_SOC liegen")
    state.boost_start_w = int(os.environ.get("PVUEB_BOOST_START_W", "500"))
    state.boost_start_soc = float(os.environ.get("PVUEB_BOOST_START_SOC", "50"))
    if state.boost_start_w < 0 or not 0 <= state.boost_start_soc <= 100:
        sys.exit("PVUEB_BOOST_START_W muss >= 0 sein, PVUEB_BOOST_START_SOC 0–100")
    # Unter boost_min_soc endet der Boost: eine damit gestartete Ladung fiele
    # sofort wieder auf den nackten PV-Überschuss zurück und stoppte.
    if state.boost_start_w and state.boost_start_soc < state.boost_min_soc:
        sys.exit("PVUEB_BOOST_START_SOC muss >= PVUEB_BOOST_MIN_SOC sein")
    state.poll_interval_s = int(os.environ.get("PVUEB_POLL_INTERVAL_S", "5"))
    state.adjust_min_interval_s = int(os.environ.get("PVUEB_ADJUST_MIN_INTERVAL_S", "25"))
    state.limit_refresh_s = int(os.environ.get("PVUEB_LIMIT_REFRESH_S", "300"))
    if state.limit_refresh_s < state.adjust_min_interval_s:
        sys.exit("PVUEB_LIMIT_REFRESH_S muss >= PVUEB_ADJUST_MIN_INTERVAL_S sein")
    state.start_delay_s = int(os.environ.get("PVUEB_START_DELAY_S", "120"))
    state.stop_delay_s = int(os.environ.get("PVUEB_STOP_DELAY_S", "180"))
    state.minpv_start_factor = float(os.environ.get("PVUEB_MINPV_START_FACTOR", "1.10"))
    state.minpv_pause_factor = float(os.environ.get("PVUEB_MINPV_PAUSE_FACTOR", "0.75"))
    state.minpv_resume_factor = float(os.environ.get("PVUEB_MINPV_RESUME_FACTOR", "0.90"))
    state.minpv_timeout_s = int(float(os.environ.get("PVUEB_MINPV_TIMEOUT_MIN", "10")) * 60)
    state.charge_w_max_age_s = int(os.environ.get("PVUEB_CHARGE_W_MAX_AGE_S", "30"))
    if state.charge_w_max_age_s < 1:
        sys.exit("PVUEB_CHARGE_W_MAX_AGE_S muss >= 1 Sekunde sein")
    state.start_retry_s = int(os.environ.get("PVUEB_START_RETRY_S", "30"))
    if state.start_retry_s < 1:
        sys.exit("PVUEB_START_RETRY_S muss >= 1 Sekunde sein")
    state.web_user = os.environ.get("PVUEB_WEB_USER", "")
    state.web_password = os.environ.get("PVUEB_WEB_PASSWORD", "")
    state.ocpp_user = os.environ.get("PVUEB_OCPP_USER", "")
    state.ocpp_password = os.environ.get("PVUEB_OCPP_PASSWORD", "")
    for name, user, password in (("PVUEB_WEB", state.web_user, state.web_password),
                                 ("PVUEB_OCPP", state.ocpp_user, state.ocpp_password)):
        if user and not password:
            sys.exit(f"{name}_USER gesetzt, aber {name}_PASSWORD fehlt")
        if user:
            log.info("%s: Anmeldung erforderlich (Benutzer %r)", name, user)
    state.record_dir = os.environ.get("PVUEB_RECORD_DIR", "")
    state.session_file = os.environ.get("PVUEB_SESSION_FILE", "")
    state.record_interval_s = int(os.environ.get("PVUEB_RECORD_INTERVAL_S", "10"))
    state.record_keep_days = int(os.environ.get("PVUEB_RECORD_KEEP_DAYS", "14"))
    if state.record_interval_s < 1 or state.record_keep_days < 1:
        sys.exit("PVUEB_RECORD_INTERVAL_S und PVUEB_RECORD_KEEP_DAYS müssen >= 1 sein")
    if not 0 < state.minpv_pause_factor < state.minpv_resume_factor <= state.minpv_start_factor:
        sys.exit("PVUEB_MINPV_*_FACTOR unplausibel (0 < pause < resume <= start nötig)")
    if min(state.poll_interval_s, state.adjust_min_interval_s,
           state.start_delay_s, state.stop_delay_s) < 1:
        sys.exit("Regelzeit-Parameter (PVUEB_*_S) müssen >= 1 Sekunde sein")
    state.lat = float(os.environ.get("PVUEB_LAT", "52.27"))
    state.lon = float(os.environ.get("PVUEB_LON", "10.52"))
    state.pv_tilt = int(os.environ.get("PVUEB_PV_TILT", "42"))
    state.pv_azimut = int(os.environ.get("PVUEB_PV_AZIMUT", "0"))
    state.pv_kwp = float(os.environ.get("PVUEB_PV_KWP", "7"))
    state.forecast_min_kwh = float(os.environ.get("PVUEB_FORECAST_MIN_KWH", "5"))
    if state.night_start_min == state.night_end_min:
        sys.exit("PVUEB_NIGHT_START und PVUEB_NIGHT_END dürfen nicht gleich sein")
    state.wallbox_user = os.environ.get("PVUEB_WALLBOX_USER", "")
    state.wallbox_password = os.environ.get("PVUEB_WALLBOX_PASSWORD", "")
    state.wallbox_id = os.environ.get("PVUEB_WALLBOX_ID", "")
    state.wallbox_poll_s = max(WALLBOX_MIN_POLL_S,
                               int(os.environ.get("PVUEB_WALLBOX_POLL_S", "60")))
    if state.wallbox_user and not (state.wallbox_password and state.wallbox_id):
        sys.exit("PVUEB_WALLBOX_USER gesetzt, aber PVUEB_WALLBOX_PASSWORD "
                 "oder PVUEB_WALLBOX_ID fehlt")
    state.notify_url = os.environ.get("PVUEB_NOTIFY_URL", "")
    state.notify_timeout_s = int(os.environ.get("PVUEB_NOTIFY_TIMEOUT_S", "5"))
    state.melde_offline_s = int(os.environ.get("PVUEB_MELDE_OFFLINE_S", "300"))
    if state.notify_timeout_s < 1 or state.melde_offline_s < 60:
        sys.exit("PVUEB_NOTIFY_TIMEOUT_S muss >= 1 s, PVUEB_MELDE_OFFLINE_S >= 60 s sein")
    state.watchdog_s = int(os.environ.get("PVUEB_WATCHDOG_S", WATCHDOG_TIMEOUT_S))
    # Untergrenze: der Wächter prüft alle WATCHDOG_CHECK_S, und ein Takt darf
    # auch mal auf einen zähen Modbus-Zugriff warten, ohne als tot zu gelten
    if state.watchdog_s < max(30, 4 * state.poll_interval_s):
        sys.exit(f"PVUEB_WATCHDOG_S muss >= {max(30, 4 * state.poll_interval_s)} sein "
                 "(mindestens ein paar Regeltakte Geduld)")
    # Konfiguration steht vollständig fest: zurückschreiben, damit die .env
    # jeden wirksamen Wert enthält statt nur die abweichenden
    write_dotenv()

    # Erst danach die Sitzung: sie darf Modus, Freigabe und die laufende
    # Transaktion überschreiben, aber keine .env-Werte.
    state.started_at = datetime.datetime.now().timestamp()
    await auf_uhr_warten()
    load_session()
    # Sofort zurückschreiben: sonst stünde bis zur ersten Änderung oder zum
    # ersten Takt gar keine oder eine veraltete Sicherung bereit — ausgerechnet
    # in den Minuten nach einem Update, in denen ein zweiter Neustart am
    # wahrscheinlichsten ist.
    save_session()
    log.info("PVueb %s (%s), Sitzung: %s", VERSION.get("beschreibung") or VERSION.get("commit"),
             VERSION.get("quelle"), state.session_note)

    server = await websockets.serve(on_connect, "0.0.0.0", args.port, subprotocols=["ocpp1.6"])
    log.info("Regel-Loop läuft. OCPP auf ws://0.0.0.0:%s/, Wechselrichter %s", args.port, args.inverter)
    # Wächter zuerst: ab hier ist ein stehender Regeltakt eine Frage von
    # Minuten, nicht von Stunden. Daemon-Thread, damit er kein Ende blockiert.
    state.last_tick = time.monotonic()
    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    log.info("Wächter aktiv: Neustart, wenn der Regeltakt %d s ausbleibt", state.watchdog_s)

    # Jede Daueraufgabe unter den Schirm. Der OCPP-Server steht daneben: er
    # läuft schon und hat mit wait_closed() nichts, was neu zu starten wäre.
    aufgaben = {
        "Regeltakt": control_task,
        "Wallbox-Verbindung": box_link_task,
        "Modbus": lambda: modbus_task(args.inverter),
        "Web-UI": lambda: web_task(args.web_port),
        "Kommandozeile": command_loop,
        "Prognose": forecast_task,
        "Sitzungssicherung": session_task,
    }
    if state.record_dir:
        aufgaben["Mitschnitt"] = record_task
    if state.wallbox_user:
        aufgaben["Wallbox-Cloud"] = wallbox_cloud_task
    if state.notify_url:
        aufgaben["Meldestelle"] = notify_task
        aufgaben["Meldungen"] = melde_task
        # Der Prozess kann seinen eigenen Tod nicht melden — er stirbt mit.
        # Was er kann, ist sagen, dass er wieder da ist, und wie lange er weg
        # war. Das ist die einzige Spur, die ein Absturz hinterlässt.
        melden("neustart",
               f"PVueb wurde neu gestartet. {state.session_note}",
               stufe="alarm", schluessel=f"neustart-{state.started_at:.0f}",
               sperre_s=0)
        log.info("Meldestelle aktiv (Ziel steht in der .env, Fristen %d s)",
                 state.melde_offline_s)
    tasks = [server.wait_closed()]
    tasks += [bewacht(name, factory) for name, factory in aufgaben.items()]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
