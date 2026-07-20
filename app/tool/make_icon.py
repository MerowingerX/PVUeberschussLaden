#!/usr/bin/env python3
"""Erzeugt das App-Icon als PNG.

Pillow ist bewusst keine Abhaengigkeit — das Icon aendert sich so gut wie nie,
und eine Bildbibliothek nur dafuer zu installieren lohnt nicht. Ergebnis geht an
flutter_launcher_icons:

    python3 tool/make_icon.py && dart run flutter_launcher_icons
"""
import struct
import zlib
from pathlib import Path

S = 1024
BG = (17, 17, 17)      # wie body-Hintergrund des Web-UI
RING = (46, 125, 50)   # wie button.on
BOLT = (238, 238, 238) # wie Text

# Blitz, zentriert und innerhalb der maskable-Safezone (mittlere 80 %), damit
# runde und quadratische Launcher-Masken nichts abschneiden.
BOLT_SHAPE = [(0.56, 0.20), (0.34, 0.545), (0.475, 0.545), (0.44, 0.80),
              (0.66, 0.455), (0.525, 0.455)]


def in_poly(px, py, pts):
    inside = False
    j = len(pts) - 1
    for i, (xi, yi) in enumerate(pts):
        xj, yj = pts[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def chunk(typ, data):
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))


def render(size, scale=1.0, transparent=False):
    """Zeichnet Ring und Blitz.

    `scale` staucht das Motiv zur Mitte: Der Vordergrund eines Adaptive Icons
    wird von den Launcher-Masken beschnitten, sichtbar bleiben nur die mittleren
    ~61 %. Bei scale=1 wuerde der Ring dort angeschnitten.
    """
    c = size / 2
    poly = [(c + (x - 0.5) * size * scale, c + (y - 0.5) * size * scale)
            for x, y in BOLT_SHAPE]
    r_out, r_in = size * 0.45 * scale, size * 0.355 * scale
    rows = []
    for y in range(size):
        row = bytearray([0])  # Filtertyp 0
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            d = ((px - c) ** 2 + (py - c) ** 2) ** 0.5
            if in_poly(px, py, poly):
                col, alpha = BOLT, 255
            elif r_in <= d <= r_out:
                col, alpha = RING, 255
            else:
                col, alpha = BG, 0
            row += bytes(col) + (bytes([alpha]) if transparent else b"")
        rows.append(bytes(row))
    colour_type = 6 if transparent else 2
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR",
                    struct.pack(">IIBBBBB", size, size, 8, colour_type, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    assets = Path(__file__).resolve().parent.parent / "assets"
    assets.mkdir(exist_ok=True)
    for name, data in (("icon.png", render(S)),
                       ("icon_foreground.png", render(S, 0.62, transparent=True))):
        (assets / name).write_bytes(data)
        print(f"{assets / name} ({(assets / name).stat().st_size} Bytes)")
