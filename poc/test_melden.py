#!/usr/bin/env python3
"""Prüft die Meldungen, die PVueb nach draußen schickt.

Aufruf:
    python test_melden.py

Am 28./29.07.2026 regelte PVueb 17 Stunden lang nicht mehr, ohne dass es
auffiel (docs/issue_nightly_load_did_not_work.md). Der Wächter macht daraus
jetzt zwei Minuten — er sagt es aber niemandem. Diese Prüfungen gelten dem,
was seither gesagt wird.

Die Regeln sitzen absichtlich in einem Beobachter neben dem Regler
(Melder.pruefen) und nicht im Regelpfad: sie lesen nur. Genau deshalb lassen
sie sich hier ohne Wallbox, ohne Wechselrichter und ohne Uhr durchspielen.
"""

import sys

import charge_loop as c

FEHLER: list[str] = []


def pruefe(bedingung, text):
    print(f"  {'ok  ' if bedingung else 'FEHL'}  {text}")
    if not bedingung:
        FEHLER.append(text)


def lage(**abweichungen) -> dict:
    """Der unauffällige Betriebsfall: alles da, nichts lädt, Tag."""
    grund = {"box_verbunden": True, "box_status": "Available", "laedt": False,
             "nacht": False, "akku_netzladung": False, "wr_alter_s": 5.0}
    grund.update(abweichungen)
    return grund


def melder(grenze_s=300, start=0.0, flanke_s=0) -> c.Melder:
    """Ein eingelaufener Melder: erster Durchgang und Schonzeit vorbei.

    flanke_s=0 heißt: Wechsel werden sofort gemeldet. Die Verweildauer hat
    einen eigenen Test — sonst müsste jede andere Prüfung sie mitrechnen.
    """
    m = c.Melder(grenze_s, start, flanke_s)
    m.pruefen(lage(), start)          # erster Durchgang merkt nur
    return m


def texte(meldungen) -> str:
    return " | ".join(m["text"] for m in meldungen)


# ------------------------------------------------------------ Anlaufverhalten

def test_erster_durchgang():
    print("Erster Durchgang")
    m = c.Melder(300, 0.0, 0)
    raus = m.pruefen(lage(box_status="Charging", laedt=True), 0.0)
    pruefe(raus == [],
           "der erste Durchgang meldet nichts — er lernt nur die Lage")

    # Ohne diese Eigenschaft meldete jeder Neustart „Fahrzeug angesteckt" und
    # „PV-Laden gestartet" für etwas, das längst lief.
    raus = m.pruefen(lage(box_status="Charging", laedt=True), 10.0)
    pruefe(raus == [], "unveränderte Lage erzeugt keine Meldung")


def test_schonzeit():
    print("Schonzeit nach dem Start")
    m = c.Melder(300, 1000.0, 0)
    m.pruefen(lage(), 1000.0)

    tot = lage(box_verbunden=False, wr_alter_s=None)
    pruefe(m.pruefen(tot, 1100.0) == [],
           "in den ersten 300 s wird kein Ausfall gemeldet")
    pruefe(m.pruefen(tot, 1200.0) == [],
           "auch kurz vor Ablauf nicht")

    # Erst nach der Schonzeit beginnt die Frist überhaupt zu laufen.
    raus = m.pruefen(tot, 1400.0)
    pruefe(raus == [], "danach beginnt die Frist von vorn")
    raus = m.pruefen(tot, 1750.0)
    pruefe(len(raus) == 2, "und nach Ablauf kommen beide Alarme")


# ------------------------------------------------------------------- Alarme

def test_wallbox_weg():
    print("Wallbox weg")
    m = melder(grenze_s=300, start=0.0)
    weg = lage(box_verbunden=False)

    pruefe(m.pruefen(weg, 400.0) == [],
           "ein kurzer Aussetzer allein meldet noch nichts")
    pruefe(m.pruefen(weg, 600.0) == [],
           "vor Ablauf der Frist bleibt es still")

    raus = m.pruefen(weg, 750.0)
    pruefe(len(raus) == 1 and raus[0]["stufe"] == "alarm",
           "nach 300 s kommt genau ein Alarm")
    pruefe("Wallbox" in raus[0]["text"] and "geladen" in raus[0]["text"],
           "und er sagt, was das bedeutet, nicht was kaputt ist")

    pruefe(m.pruefen(weg, 1000.0) == [],
           "derselbe Ausfall wird nicht noch einmal gemeldet")

    zurueck = m.pruefen(lage(), 1100.0)
    pruefe(len(zurueck) == 1 and zurueck[0]["stufe"] == "info",
           "die Rückkehr wird gemeldet — sonst weiß niemand, ob der Alarm gilt")

    pruefe(m.pruefen(lage(), 1200.0) == [],
           "danach ist wieder Ruhe")

    # Zweiter Ausfall muss wieder melden: der Merker wurde zurückgesetzt.
    m.pruefen(weg, 1300.0)
    raus = m.pruefen(weg, 1700.0)
    pruefe(len(raus) == 1, "ein zweiter Ausfall meldet erneut")


def test_wechselrichter_weg():
    print("Wechselrichter weg")
    m = melder(grenze_s=300, start=0.0)

    pruefe(m.pruefen(lage(wr_alter_s=200.0), 400.0) == [],
           "ein zäher Modbus-Zugriff ist kein Ausfall")

    m.pruefen(lage(wr_alter_s=400.0), 500.0)
    raus = m.pruefen(lage(wr_alter_s=700.0), 850.0)
    pruefe(len(raus) == 1 and raus[0]["stufe"] == "alarm",
           "veraltete Messwerte lösen nach der Frist Alarm aus")
    pruefe("Überschuss" in raus[0]["text"],
           "der Text nennt die Folge: der Überschuss ist unbekannt")

    m2 = melder(grenze_s=300, start=0.0)
    m2.pruefen(lage(wr_alter_s=None), 400.0)
    raus = m2.pruefen(lage(wr_alter_s=None), 750.0)
    pruefe(len(raus) == 1, "nie gesehene Messwerte gelten ebenfalls als Ausfall")


def test_beide_weg():
    print("Beide weg")
    m = melder(grenze_s=300, start=0.0)
    tot = lage(box_verbunden=False, wr_alter_s=None)
    m.pruefen(tot, 400.0)
    raus = m.pruefen(tot, 750.0)
    pruefe(len(raus) == 2, "Wallbox und Wechselrichter melden getrennt")
    pruefe(all(x["stufe"] == "alarm" for x in raus), "beides sind Alarme")


# --------------------------------------------------------------------- Info

def test_fahrzeug():
    print("Fahrzeug")
    m = melder()

    raus = m.pruefen(lage(box_status="Preparing"), 400.0)
    pruefe(len(raus) == 1 and raus[0]["stufe"] == "info"
           and "angesteckt" in raus[0]["text"], "Anstecken wird gemeldet")

    pruefe(m.pruefen(lage(box_status="Charging"), 410.0) == [],
           "Preparing -> Charging ist kein zweites Anstecken")
    pruefe(m.pruefen(lage(box_status="SuspendedEV"), 420.0) == [],
           "SuspendedEV auch nicht — das Kabel steckt weiter")

    raus = m.pruefen(lage(box_status="Available"), 430.0)
    pruefe(len(raus) == 1 and "abgesteckt" in raus[0]["text"],
           "Abstecken wird gemeldet")


def test_laden():
    print("Laden")
    m = melder()

    raus = m.pruefen(lage(laedt=True), 400.0)
    pruefe(len(raus) == 1 and raus[0]["thema"] == "pvladung"
           and "PV-Laden gestartet" in raus[0]["text"],
           "am Tag ist es PV-Laden")

    raus = m.pruefen(lage(laedt=False), 500.0)
    pruefe("PV-Laden beendet" in texte(raus), "und das Ende auch")

    m2 = melder()
    raus = m2.pruefen(lage(laedt=True, nacht=True), 400.0)
    pruefe(raus[0]["thema"] == "nachtladung"
           and "Nachtladen gestartet" in raus[0]["text"],
           "im Nachtfenster ist es Nachtladen")

    # Eine Nachtladung, die um 08:05 endet, ist immer noch eine Nachtladung.
    # Würde beim Stopp das aktuelle Fenster gelesen, hieße sie „PV-Laden
    # beendet" — und im Chat stünde ein Ende ohne Anfang.
    raus = m2.pruefen(lage(laedt=False, nacht=False), 500.0)
    pruefe("Nachtladen beendet" in texte(raus),
           "das Ende trägt die Art, mit der die Ladung begann")


def test_hausakku():
    print("Hausakku")
    m = melder()

    raus = m.pruefen(lage(akku_netzladung=True), 400.0)
    pruefe(len(raus) == 1 and raus[0]["thema"] == "hausakku"
           and "aus dem Netz" in raus[0]["text"],
           "Beginn der Netzladung wird gemeldet")

    pruefe(m.pruefen(lage(akku_netzladung=True), 410.0) == [],
           "die laufende Netzladung meldet nicht weiter")

    raus = m.pruefen(lage(akku_netzladung=False), 500.0)
    pruefe("beendet" in texte(raus), "das Ende ebenfalls")


# -------------------------------------------------------------- Warteschlange

def test_warteschlange():
    print("Warteschlange")
    c.meldungen.clear()
    c.state.notify_dropped = 0

    c.melden("probe", "eine Meldung", stufe="alarm")
    pruefe(len(c.meldungen) == 1, "melden() reiht ein")
    nachricht = c.meldungen[0]["nachricht"]
    pruefe(nachricht["quelle"] == "pvueb" and nachricht["stufe"] == "alarm",
           "und baut das JSON, das die Meldestelle erwartet")
    pruefe(nachricht["schluessel"] == "probe-alarm",
           "ohne eigenen Schlüssel entsteht einer aus Thema und Stufe")

    # Unbegrenzt wäre die Warteschlange ein Speicherleck mit Anlauf: eine
    # flatternde Quelle bei totem Empfänger füllt sie in Minuten.
    c.meldungen.clear()
    c.state.notify_dropped = 0
    for i in range(c.NOTIFY_QUEUE_MAX + 50):
        c.melden("probe", f"Meldung {i}")
    pruefe(len(c.meldungen) == c.NOTIFY_QUEUE_MAX,
           "die Warteschlange wächst nicht über ihre Grenze")
    pruefe(c.state.notify_dropped == 50, "die verworfenen werden gezählt")
    pruefe("Meldung 249" in c.meldungen[-1]["nachricht"]["text"],
           "das Neueste bleibt, das Älteste fliegt")

    c.meldungen.clear()
    c.state.notify_dropped = 0


def test_verweildauer():
    print("Verweildauer vor der Meldung")
    m = melder(flanke_s=120)

    # Der Fall vom 29.07.2026: die Wallbox meldete abwechselnd den Status der
    # Dose und den der Station, und daraus wurde "angesteckt / abgesteckt" im
    # Minutentakt. Ein Pendeln darf gar keine Meldung erzeugen, nicht bloß
    # weniger.
    pruefe(m.pruefen(lage(box_status="Preparing"), 400.0) == [],
           "ein frischer Wechsel meldet noch nicht")
    pruefe(m.pruefen(lage(box_status="Available"), 460.0) == [],
           "der Rücksprung ebenfalls nicht")
    pruefe(m.pruefen(lage(box_status="Preparing"), 520.0) == [],
           "und das Hin und Her erzeugt nichts")
    pruefe(m.pruefen(lage(box_status="Available"), 580.0) == [],
           "auch nach vier Wechseln nicht")

    # Erst wenn ein Zustand steht, geht die Meldung raus.
    pruefe(m.pruefen(lage(box_status="Preparing"), 640.0) == [],
           "ein neuer Kandidat beginnt seine Frist von vorn")
    raus = m.pruefen(lage(box_status="Preparing"), 800.0)
    pruefe(len(raus) == 1 and "angesteckt" in raus[0]["text"],
           "nach 120 s gehaltenem Zustand kommt genau eine Meldung")
    pruefe(m.pruefen(lage(box_status="Preparing"), 900.0) == [],
           "danach ist Ruhe")


def test_station_und_dose():
    print("Station und Dose (OCPP connectorId)")
    c.state.connector_status.clear()
    c.state.box_status = "unbekannt"

    c.box_status_setzen(1, "SuspendedEV")
    pruefe(c.state.box_status == "SuspendedEV", "die Dose bestimmt den Status")

    # connectorId 0 ist die Station: "Available" heißt dort betriebsbereit,
    # nicht "da steckt nichts".
    c.box_status_setzen(0, "Available")
    pruefe(c.state.box_status == "SuspendedEV",
           "eine Station-Meldung überschreibt den Dosenstatus nicht")

    c.box_status_setzen(1, "Charging")
    pruefe(c.state.box_status == "Charging", "die Dose zählt weiter")

    # Es gibt Boxen, die ausschließlich für 0 melden — die dürfen nicht blind
    # bleiben.
    c.state.connector_status.clear()
    c.state.box_status = "unbekannt"
    c.box_status_setzen(0, "Preparing")
    pruefe(c.state.box_status == "Preparing",
           "ohne bekannte Dose gilt die Station")

    c.state.connector_status.clear()
    c.state.box_status = "unbekannt"


def main():
    test_erster_durchgang()
    test_schonzeit()
    test_wallbox_weg()
    test_wechselrichter_weg()
    test_beide_weg()
    test_fahrzeug()
    test_laden()
    test_hausakku()
    test_verweildauer()
    test_station_und_dose()
    test_warteschlange()

    print()
    if FEHLER:
        print(f"{len(FEHLER)} Prüfung(en) fehlgeschlagen:")
        for text in FEHLER:
            print(f"  - {text}")
        return 1
    print("alle Prüfungen bestanden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
