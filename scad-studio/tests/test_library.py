"""Tests für die OpenSCAD-Bibliothek scadstudio/library/studio.scad.

Alle Tests rendern mit der installierten OpenSCAD-Version (auch 2021.01) und
werden übersprungen, wenn ``openscad`` nicht im PATH liegt.

Aufruf:  cd scad-studio && python -m unittest tests.test_library

Geprüft wird:
* jedes Modul rendert ohne ERROR/WARNING, das STL ist nicht leer, geschlossen
  (jede Kante genau zweimal, gegenläufig) und hat positives Volumen;
* CGAL-Boolesche Operationen mit jedem Teil funktionieren;
* PASSUNG: intersection(Stecker, Block − Buchse) ist LEER – und als
  Gegenprobe mit negativem Spaltmaß bzw. verschobenem Stecker NICHT leer;
* Gewinde-Phase: zwei Vierkant-Module mit M24×3 fluchten im festgezogenen
  Zustand; um 60° verdreht kollidieren sie;
* Renderzeiten typischer Fälle; Beispiele aus STUDIO_LIB.md rendern.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "scadstudio" / "library"
LIB = LIB_DIR / "studio.scad"
DOC = LIB_DIR / "STUDIO_LIB.md"
OPENSCAD = shutil.which("openscad")
EMPTY_MSG = "Current top level object is empty"
TIMEOUT = 300


# ---------------------------------------------------------------------------
# Rendern + STL-Auswertung
# ---------------------------------------------------------------------------

@dataclass
class Result:
    code: str
    rc: int | None
    log: str
    seconds: float
    problems: list[str] = field(default_factory=list)
    empty: bool = False
    triangles: int = 0
    volume: float = 0.0
    open_edges: int = 0
    bbox: tuple = ((0, 0, 0), (0, 0, 0))
    echoes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (f"rc={self.rc} empty={self.empty} tris={self.triangles} vol={self.volume:.2f} "
                f"open_edges={self.open_edges} problems={self.problems[:5]}\n--- code ---\n{self.code}")


def read_stl(path: Path) -> list[tuple]:
    data = path.read_bytes()
    if data[:5] == b"solid" and b"facet" in data[:400]:
        tris, cur = [], []
        for line in data.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if parts[:1] == ["vertex"]:
                cur.append(tuple(float(x) for x in parts[1:4]))
                if len(cur) == 3:
                    tris.append(tuple(cur))
                    cur = []
        return tris
    count = struct.unpack("<I", data[80:84])[0]
    tris = []
    for i in range(count):
        vals = struct.unpack("<12f", data[84 + 50 * i: 84 + 50 * i + 48])
        tris.append((vals[3:6], vals[6:9], vals[9:12]))
    return tris


def mesh_stats(tris: list[tuple]) -> tuple[float, int, tuple]:
    """Volumen (Vorzeichen = Orientierung), Zahl offener/falscher Kanten, Bounding Box."""
    vol = 0.0
    edges: Counter = Counter()
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for a, b, c in tris:
        vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
        for p in (a, b, c):
            for i in range(3):
                lo[i] = min(lo[i], p[i])
                hi[i] = max(hi[i], p[i])
        for p, q in ((a, b), (b, c), (c, a)):
            edges[(p, q)] += 1
    bad = sum(1 for (p, q), n in edges.items() if n != 1 or edges.get((q, p), 0) != 1)
    return vol, bad, (tuple(lo), tuple(hi))


def render(code: str, with_lib: bool = True) -> Result:
    """Rendert Code (mit ``use <studio.scad>``) zu STL und wertet das Protokoll aus."""
    with tempfile.TemporaryDirectory(prefix="studio-lib-") as tmp:
        scad = Path(tmp) / "model.scad"
        out = Path(tmp) / "model.stl"
        scad.write_text(("use <studio.scad>\n" if with_lib else "") + code, encoding="utf-8")
        env = dict(os.environ, OPENSCADPATH=str(LIB_DIR))
        start = time.monotonic()
        try:
            proc = subprocess.run([OPENSCAD, "-o", str(out), "--export-format", "binstl", str(scad)],
                                  capture_output=True, text=True, errors="replace", env=env,
                                  cwd=tmp, timeout=TIMEOUT)
            rc, log = proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            rc, log = None, "ERROR: timeout"
        seconds = time.monotonic() - start
        res = Result(code=code, rc=rc, log=log, seconds=seconds)
        for line in log.splitlines():
            s = line.strip()
            up = s.upper()
            if up.startswith(("WARNING", "ERROR", "DEPRECATED", "CGAL ERROR")) or "PARSER ERROR" in up \
                    or "NOT A VALID 2-MANIFOLD" in up or "NOT CLOSED" in up:
                res.problems.append(s)
            if up.startswith("ECHO"):
                res.echoes.append(s)
        res.empty = EMPTY_MSG in log
        if out.exists() and out.stat().st_size > 84:
            tris = read_stl(out)
            res.triangles = len(tris)
            res.volume, res.open_edges, res.bbox = mesh_stats(tris)
        return res


_CACHE: dict[str, Result] = {}


def get(code: str) -> Result:
    if code not in _CACHE:
        _CACHE[code] = render(code)
    return _CACHE[code]


# ---------------------------------------------------------------------------
# Testfälle
# ---------------------------------------------------------------------------

BLOCK = "translate([-15, -15, 0]) cube([30, 30, 15]);"
BIG = "translate([-25, -25, 0]) cube([50, 50, 30]);"


def neg(shape: str, block: str = BLOCK) -> str:
    """Negative Form in einen Block schneiden (zugleich CGAL-Boolesch-Test)."""
    return f"difference() {{ {block} {shape} }}"


def cut(shape: str) -> str:
    """Positives Teil mit CGAL schräg anschneiden (Boolesch-Test fürs Polyeder)."""
    return f"difference() {{ {shape} translate([1.5, 0, 0]) rotate(7) translate([0, -200, -200]) cube(400); }}"


def fit(male: str, female: str, block: str) -> str:
    """Schnitt Stecker ∩ (Block − Buchse): leer = passt."""
    return f"intersection() {{ {male} difference() {{ {block} {female} }} }}"


# Positive Teile (werden zusätzlich per CGAL halbiert)
POSITIVE = {
    "metric_thread_ext": "metric_thread(24, 3, 20);",
    "metric_thread_left": "metric_thread(16, length = 12, left = true);",
    "metric_thread_printable": "metric_thread(24, 3, 12, profile = \"printable\");",
    "metric_thread_bore": "metric_thread(24, 3, 12, bore = 8);",
    "metric_thread_small": "metric_thread(3, length = 6);",
    "threaded_rod": "threaded_rod(8, 30);",
    "bolt_hex": "bolt(8, 20, head = \"hex\");",
    "bolt_socket": "bolt(6, 16, head = \"socket\", thread_length = 10);",
    "bolt_countersunk": "bolt(8, 20, head = \"countersunk\");",
    "bolt_button": "bolt(5, 12, head = \"button\");",
    "bolt_none": "bolt(10, 20, head = \"none\");",
    "nut_m8": "nut(8);",
    "nut_m24": "nut(24);",
    "thread_male": "thread_male(24, 3, 12);",
    "thread_male_bore_flip": "thread_male(24, 3, 12, bore = 8, flip = true);",
    "peg": "peg(5, 8);",
    "d_peg": "d_peg(6, 8);",
    "square_plug": "square_plug([12, 8], 10, r = 2);",
    "dovetail": "dovetail(10, 5, 20);",
    "snap_hook": "snap_hook(12, 6);",
    "bayonet_male": "bayonet_male(30, 10);",
    "rounded_box_z": "rounded_box([30, 20, 10], 3);",
    "rounded_box_top": "rounded_box([30, 20, 10], 3, center = true, edges = \"top\");",
    "rounded_box_all": "rounded_box(20, 4, edges = \"all\");",
    "chamfer_cylinder": "chamfer_cylinder(16, 12, 0.6, 1.5);",
    "elephant_foot_extrude": "elephant_foot_extrude(8, 0.6) difference() { square([20, 10]); translate([5, 3]) square(4); }",
}

# Negative Teile (in einen Block geschnitten)
NEGATIVE = {
    "metric_thread_int": neg("metric_thread(12, length = 15, internal = true);"),
    "threaded_hole": neg("threaded_hole(12, 12);"),
    "threaded_hole_through_flip": neg("translate([0, 0, 15]) threaded_hole(10, 15, through = true, flip = true);"),
    "thread_female": neg("thread_female(24, 3, 12);", BIG),
    "screw_hole_socket": neg("screw_hole(3, 15, head = \"socket\");"),
    "screw_hole_button": neg("screw_hole(4, 15, head = \"button\", sacrificial = 0.2);"),
    "screw_hole_countersunk": neg("screw_hole(5, 15, head = \"countersunk\", head_depth = 1);"),
    "screw_hole_hex_flip": neg("translate([0, 0, 15]) screw_hole(6, 15, head = \"hex\", flip = true);"),
    "screw_hole_none_fine": neg("screw_hole(8, 15, head = \"none\", fit = \"fine\");"),
    "nut_trap_slot": neg("nut_trap(3, slot = 20);"),
    "heatset_m2": neg("heatset_hole(2);"),
    "heatset_m3": neg("heatset_hole(3);"),
    "heatset_m5_flip": neg("translate([0, 0, 15]) heatset_hole(5, flip = true);"),
    "heatset_m8": neg("heatset_hole(8);"),
    "magnet_pocket": neg("magnet_pocket(10, 2);"),
    "lamp_nipple_mount": neg("lamp_nipple_mount(15);"),
    "peg_hole": neg("peg_hole(5, 8);"),
    "d_peg_hole": neg("d_peg_hole(6, 8);"),
    "square_socket": neg("square_socket([12, 8], 10, r = 2);"),
    "dovetail_slot": neg("dovetail_slot(10, 5, 40);"),
    "snap_window": neg("snap_window(12, 6);", "translate([0.05, -10, 1.5]) cube([3, 20, 16]);"),
    "bayonet_socket": neg("bayonet_socket(30, 10);", BIG),
    "teardrop_hole": neg("translate([-15, 0, 7]) teardrop_hole(5, 30);"),
    "teardrop_truncated": neg("teardrop_hole(5, 20, center = true, truncate = true);", "translate([-8, -8, -8]) cube(16);"),
    "cable_channel": neg("translate([-15, 0, 7]) cable_channel(6, 30);"),
    "cable_channel_round": neg("cable_channel(4, 20, teardrop = false, center = true);", "translate([-8, -8, -8]) cube(16);"),
    "cable_path": neg("cable_path([[0, -20, 5], [0, 0, 5], [0, 0, 20]]);"),
}

# Passungs-Tests: (Code, erwartet_leer)
B24 = "translate([-20, -20, 0]) cube([40, 40, 25]);"
FITS = {
    # Gewinde M24×3 (zentriertes Innengewinde, seated=false)
    "m24": (fit("metric_thread(24, 3, 20);", "metric_thread(24, 3, 20, internal = true, seated = false);", B24), True),
    "m24_oversize": (fit("metric_thread(24, 3, 20);", "metric_thread(24, 3, 20, internal = true, seated = false, tol = -0.1);", B24), False),
    # Axialspiel: tol/cos30 = 0.23 mm je Seite → 0.18 passt, 0.30 kollidiert
    "m24_up_0.18": (fit("translate([0, 0, 0.18]) metric_thread(24, 3, 20);", "metric_thread(24, 3, 21, internal = true, seated = false);", B24), True),
    "m24_up_0.30": (fit("translate([0, 0, 0.30]) metric_thread(24, 3, 20);", "metric_thread(24, 3, 21, internal = true, seated = false);", B24), False),
    "m24_down_0.18": (fit("translate([0, 0, -0.18]) metric_thread(24, 3, 20);", "translate([0, 0, -3]) metric_thread(24, 3, 24, internal = true, seated = false, lead_in = false);", B24), True),
    "m24_down_0.30": (fit("translate([0, 0, -0.30]) metric_thread(24, 3, 20);", "translate([0, 0, -3]) metric_thread(24, 3, 24, internal = true, seated = false, lead_in = false);", B24), False),
    # Radialspiel 0.2 mm
    "m24_x_0.15": (fit("translate([0.15, 0, 0]) metric_thread(24, 3, 20);", "metric_thread(24, 3, 20, internal = true, seated = false);", B24), True),
    "m24_x_0.30": (fit("translate([0.30, 0, 0]) metric_thread(24, 3, 20);", "metric_thread(24, 3, 20, internal = true, seated = false);", B24), False),
    "m12": (fit("metric_thread(12, 1.75, 15);", "metric_thread(12, 1.75, 15, internal = true, seated = false);", BLOCK), True),
    "m12_oversize": (fit("metric_thread(12, 1.75, 15);", "metric_thread(12, 1.75, 15, internal = true, seated = false, tol = -0.1);", BLOCK), False),
    "m8": (fit("metric_thread(8, 1.25, 12);", "metric_thread(8, 1.25, 12, internal = true);", BLOCK), True),
    "m8_oversize": (fit("metric_thread(8, 1.25, 12);", "metric_thread(8, 1.25, 12, internal = true, tol = -0.1);", BLOCK), False),
    "m8_up_0.2": (fit("translate([0, 0, 0.2]) metric_thread(8, 1.25, 12);", "metric_thread(8, 1.25, 13, internal = true, seated = false);", BLOCK), True),
    "m8_up_0.3": (fit("translate([0, 0, 0.3]) metric_thread(8, 1.25, 12);", "metric_thread(8, 1.25, 13, internal = true, seated = false);", BLOCK), False),
    "m24_printable": (fit("metric_thread(24, 3, 20, profile = \"printable\");", "metric_thread(24, 3, 20, internal = true, profile = \"printable\");", B24), True),
    "m24_printable_oversize": (fit("metric_thread(24, 3, 20, profile = \"printable\");", "metric_thread(24, 3, 20, internal = true, profile = \"printable\", tol = -0.1);", B24), False),
    "m24_left": (fit("metric_thread(24, 3, 20, left = true);", "metric_thread(24, 3, 20, internal = true, left = true);", B24), True),
    "m24_left_vs_right": (fit("metric_thread(24, 3, 20, left = true);", "metric_thread(24, 3, 20, internal = true);", B24), False),
    "global_tol_0.3": (fit("translate([0, 0, 0.3]) metric_thread(24, 3, 20);", "$tol = 0.3; metric_thread(24, 3, 21, internal = true, seated = false);", B24), True),
    # Schraube + Mutter (Mutter um ganze Steigungen versetzt → gleiche Phase)
    "bolt_nut": ("intersection() { bolt(8, 25); translate([0, 0, iso_hex_head(8)[1] + 8 * 1.25]) nut(8); }", True),
    "bolt_nut_oversize": ("intersection() { bolt(8, 25); translate([0, 0, iso_hex_head(8)[1] + 8 * 1.25]) nut(8, tol = -0.1); }", False),
    # Rundzapfen
    "peg": (fit("peg(5, 8);", "peg_hole(5, 8);", BLOCK), True),
    "peg_coarse_fn": (fit("peg(5, 8, $fn = 7);", "peg_hole(5, 8, $fn = 5);", BLOCK), True),
    "peg_x_0.15": (fit("translate([0.15, 0, 0]) peg(5, 8);", "peg_hole(5, 8);", BLOCK), True),
    "peg_x_0.25": (fit("translate([0.25, 0, 0]) peg(5, 8);", "peg_hole(5, 8);", BLOCK), False),
    "peg_oversize": (fit("peg(5, 8);", "peg_hole(5, 8, tol = -0.1);", BLOCK), False),
    # D-Zapfen
    "d_peg": (fit("d_peg(6, 8);", "d_peg_hole(6, 8);", BLOCK), True),
    "d_peg_x_0.15": (fit("translate([0.15, 0, 0]) d_peg(6, 8);", "d_peg_hole(6, 8);", BLOCK), True),
    "d_peg_x_0.25": (fit("translate([0.25, 0, 0]) d_peg(6, 8);", "d_peg_hole(6, 8);", BLOCK), False),
    "d_peg_rotated": (fit("rotate(20) d_peg(6, 8);", "d_peg_hole(6, 8);", BLOCK), False),
    "d_peg_oversize": (fit("d_peg(6, 8);", "d_peg_hole(6, 8, tol = -0.1);", BLOCK), False),
    # Vierkant
    "square": (fit("square_plug(10, 10);", "square_socket(10, 10);", BLOCK), True),
    "square_rounded": (fit("square_plug([12, 8], 10, r = 2);", "square_socket([12, 8], 10, r = 2);", BLOCK), True),
    "square_xyz_0.15": (fit("translate([0.15, 0.15, 0.15]) square_plug(10, 10);", "square_socket(10, 10);", BLOCK), True),
    "square_x_0.25": (fit("translate([0.25, 0, 0]) square_plug(10, 10);", "square_socket(10, 10);", BLOCK), False),
    "square_oversize": (fit("square_plug(10, 10);", "square_socket(10, 10, tol = -0.1);", BLOCK), False),
    # Schwalbenschwanz
    "dovetail": (fit("dovetail(10, 5, 20);", "dovetail_slot(10, 5, 20);", "translate([-15, -9, 0]) cube([30, 18, 10]);"), True),
    "dovetail_shift_0.15": (fit("translate([0.15, 0, 0.1]) dovetail(10, 5, 20);", "dovetail_slot(10, 5, 20);", "translate([-15, -9, 0]) cube([30, 18, 10]);"), True),
    "dovetail_x_0.25": (fit("translate([0.25, 0, 0]) dovetail(10, 5, 20);", "dovetail_slot(10, 5, 20);", "translate([-15, -9, 0]) cube([30, 18, 10]);"), False),
    "dovetail_oversize": (fit("dovetail(10, 5, 20);", "dovetail_slot(10, 5, 20, tol = -0.1);", "translate([-15, -9, 0]) cube([30, 18, 10]);"), False),
    # Schnapphaken (Gegenwand bei x >= 0.05, oberhalb der Kehle)
    "snap": (fit("snap_hook(12, 6);", "snap_window(12, 6);", "translate([0.05, -10, 1.5]) cube([3, 20, 16]);"), True),
    "snap_oversize": (fit("snap_hook(12, 6);", "snap_window(12, 6, tol = -0.1);", "translate([0.05, -10, 1.5]) cube([3, 20, 16]);"), False),
    # Bajonett: eingesetzt (0°), halb (15°) und verriegelt (30°) frei; 45° = Anschlag
    "bayonet_0": (fit("bayonet_male(30, 10);", "bayonet_socket(30, 10);", BIG), True),
    "bayonet_15": (fit("rotate(15) bayonet_male(30, 10);", "bayonet_socket(30, 10);", BIG), True),
    "bayonet_locked": (fit("rotate(30) bayonet_male(30, 10);", "bayonet_socket(30, 10);", BIG), True),
    "bayonet_overturned": (fit("rotate(45) bayonet_male(30, 10);", "bayonet_socket(30, 10);", BIG), False),
    "bayonet_oversize": (fit("rotate(30) bayonet_male(30, 10);", "bayonet_socket(30, 10, tol = -0.1);", BIG), False),
    # Magnet / Lampennippel mit Mutter
    "magnet": (fit("cylinder(d = 6, h = 3, $fn = 64);", "magnet_pocket(6, 3);", BLOCK), True),
    "lamp_nipple_with_nut": (fit("cylinder(d = 10, h = 15, $fn = 64); cylinder(r = 14 / sqrt(3), h = 3, $fn = 6);",
                                 "lamp_nipple_mount(15);", BLOCK), True),
}


def phase_case(rot: float, male: str = "thread_male(24, 3, 12);",
               female: str = "thread_female(24, 3, 12);") -> str:
    """Zwei Vierkant-Module (40×40) mit M24×3: Stecker auf der Schulter z=0, Buchse im
    Boden des oberen Blocks (festgezogene Lage, gleiche Ausrichtung), Buchse um rot° gedreht."""
    return (f"intersection() {{ {male} rotate({rot}) difference() {{ "
            f"translate([-20, -20, 0]) cube([40, 40, 30]); {female} }} }}")


PHASE = {
    "aligned": (phase_case(0), True),
    "rotated_60": (phase_case(60), False),
    # Anschlag im Uhrzeigersinn (Anziehrichtung) wenige Grad nach 0°, Spiel zum Lösen
    "tighten_10": (phase_case(-10), False),
    "loosen_30": (phase_case(30), True),
    # phase-Parameter: Buchse mit phase=90 passt erst um -90° gedreht
    "phase90_unrotated": (phase_case(0, female="thread_female(24, 3, 12, phase = 90);"), False),
    "phase90_rotated": (phase_case(-90, female="thread_female(24, 3, 12, phase = 90);"), True),
    # Stecker mit Kabelbohrung
    "aligned_bore": (phase_case(0, male="thread_male(24, 3, 12, bore = 8);"), True),
    # nach unten zeigender Stecker (flip) in nach unten offene Buchse
    "flip_aligned": ("intersection() { thread_male(24, 3, 12, flip = true, phase = 30); difference() { "
                     "translate([-20, -20, -30]) cube([40, 40, 30]); thread_female(24, 3, 12, flip = true, phase = 30); } }", True),
    "flip_rotated_60": ("intersection() { thread_male(24, 3, 12, flip = true, phase = 30); rotate(60) difference() { "
                        "translate([-20, -20, -30]) cube([40, 40, 30]); thread_female(24, 3, 12, flip = true, phase = 30); } }", False),
}


def doc_examples() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```openscad\n(.*?)```", text, flags=re.S)
    # 'use <studio.scad>' steht im Beispiel selbst → ohne Präfix rendern
    return blocks


def all_cached_codes() -> list[str]:
    codes = list(POSITIVE.values()) + [cut(c) for c in POSITIVE.values()] + list(NEGATIVE.values())
    codes += [c for c, _ in FITS.values()] + [c for c, _ in PHASE.values()]
    return codes


def setUpModule():  # noqa: N802 – unittest-Konvention
    if not OPENSCAD:
        return
    codes = [c for c in dict.fromkeys(all_cached_codes()) if c not in _CACHE]
    workers = max(1, min(4, os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code, res in zip(codes, pool.map(render, codes)):
            _CACHE[code] = res


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@unittest.skipIf(OPENSCAD is None, "openscad nicht installiert")
class LibraryTestCase(unittest.TestCase):
    def assertClean(self, res: Result):
        self.assertEqual(res.problems, [], res.describe())
        self.assertEqual(res.rc, 0, res.describe())
        self.assertFalse(res.empty, res.describe())
        self.assertGreater(res.triangles, 0, res.describe())
        self.assertEqual(res.open_edges, 0, "Netz nicht geschlossen\n" + res.describe())
        self.assertGreater(res.volume, 0, "Volumen <= 0 (Orientierung?)\n" + res.describe())

    def assertFit(self, res: Result, expect_empty: bool):
        self.assertEqual(res.problems, [], res.describe())
        if expect_empty:
            self.assertTrue(res.empty, "Teile überschneiden sich (kein Spiel)\n" + res.describe())
        else:
            self.assertFalse(res.empty, "Gegenprobe: Überschneidung erwartet\n" + res.describe())
            self.assertEqual(res.rc, 0, res.describe())
            self.assertGreater(res.volume, 0, res.describe())


class TestRenderClean(LibraryTestCase):
    def test_positive_parts_render_clean(self):
        for name, code in POSITIVE.items():
            with self.subTest(name):
                self.assertClean(get(code))

    def test_positive_parts_cgal_boolean(self):
        for name, code in POSITIVE.items():
            with self.subTest(name):
                self.assertClean(get(cut(code)))

    def test_negative_parts_cut_clean(self):
        for name, code in NEGATIVE.items():
            with self.subTest(name):
                self.assertClean(get(code))

    def test_no_warning_without_tol(self):
        # $tol / $nozzle sind nirgends gesetzt → trotzdem keine "unknown variable"-Warnung
        res = get("peg_hole(5, 8); metric_thread(8, length = 5, internal = true);")
        self.assertEqual(res.problems, [], res.describe())

    def test_bed_outline_is_preview_only(self):
        res = get("bed_outline(); cube(10);")
        self.assertClean(res)
        (lo, hi) = res.bbox
        self.assertAlmostEqual(lo[2], 0.0, places=3)
        self.assertAlmostEqual(hi[0], 10.0, places=3)

    def test_thread_dimensions(self):
        res = get("metric_thread(24, 3, 20);")
        (lo, hi) = res.bbox
        self.assertLessEqual(hi[0], 12.0 + 1e-3)          # Außen-Ø nominal
        self.assertGreater(hi[0], 11.9)
        self.assertAlmostEqual(hi[2], 20.0, places=3)
        self.assertAlmostEqual(lo[2], 0.0, places=3)

    def test_thread_facet_budget(self):
        # M24×3, 20 mm: schlankes Netz (Standardauflösung)
        self.assertLess(get("metric_thread(24, 3, 20);").triangles, 6000)


class TestFunctions(LibraryTestCase):
    def test_table_functions(self):
        code = ("echo(v = [iso_pitch(3), iso_pitch(8), iso_pitch(24), iso_pitch(36), iso_pitch(26), iso_pitch(80),"
                " iso_clearance(3), iso_clearance(8), iso_clearance(3, \"fine\"), iso_nut_s(8), iso_nut_m(8),"
                " iso_socket_head(3)[0], counterbore_d(3), iso_csk_head(3)[0], heatset_dims(3)[0], heatset_dims(4)[1],"
                " fits_bed([200, 200, 100]) ? 1 : 0, fits_bed([230, 100, 10]) ? 1 : 0, fits_bed([100, 210, 260]) ? 1 : 0]);"
                " cube(1);")
        res = get(code)
        self.assertClean(res)
        self.assertEqual(len(res.echoes), 1, res.log)
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", res.echoes[0].split("=", 1)[1])]
        expected = [0.5, 1.25, 3, 4, 3, 6, 3.4, 9, 3.2, 13, 6.8, 5.5, 6.5, 6.72, 4.0, 8.1, 1, 0, 0]
        self.assertEqual(len(nums), len(expected), res.echoes)
        for got, exp in zip(nums, expected):
            self.assertAlmostEqual(got, exp, places=3, msg=res.echoes[0])

    def test_global_tol_is_used(self):
        res = get("$tol = 0.35; echo(t = _tol(undef), t2 = _tol(0.1)); cube(1);")
        self.assertIn("ECHO: t = 0.35, t2 = 0.1", res.log)


class TestFits(LibraryTestCase):
    def test_fits(self):
        for name, (code, expect_empty) in FITS.items():
            with self.subTest(name):
                self.assertFit(get(code), expect_empty)


class TestThreadPhase(LibraryTestCase):
    """Vierkant-Lampenmodule mit M24×3 müssen im festgezogenen Zustand fluchten."""

    def test_phase(self):
        for name, (code, expect_empty) in PHASE.items():
            with self.subTest(name):
                self.assertFit(get(code), expect_empty)


class TestRenderTimes(LibraryTestCase):
    """Typische Renderzeiten (frisch, nicht aus dem Cache)."""

    CASES = {
        "M24x3 20 mm Aussengewinde": "thread_male(24, 3, 20);",
        "M24x3 20 mm Innengewinde in Block": neg("thread_female(24, 3, 20);", BIG),
        "M24x3 Passungstest (intersection)": fit("thread_male(24, 3, 20);", "thread_female(24, 3, 20);", BIG),
        "M8x30 Sechskantschraube": "bolt(8, 30);",
        "M24 Mutter": "nut(24);",
    }

    def test_render_times(self):
        lines = []
        for name, code in self.CASES.items():
            res = render(code)
            lines.append(f"  {name:36s} {res.seconds:6.2f} s  ({res.triangles} Dreiecke)")
            with self.subTest(name):
                self.assertEqual(res.problems, [], res.describe())
                self.assertLess(res.seconds, 60, f"{name} zu langsam: {res.seconds:.1f} s")
        sys.stderr.write("\nRenderzeiten (" + subprocess.run([OPENSCAD, "--version"], capture_output=True,
                                                                text=True).stderr.strip() + "):\n"
                         + "\n".join(lines) + "\n")


class TestDocExamples(LibraryTestCase):
    def test_examples_render(self):
        examples = doc_examples()
        self.assertGreaterEqual(len(examples), 4)
        for i, code in enumerate(examples, 1):
            with self.subTest(example=i):
                self.assertIn("use <studio.scad>", code)
                self.assertClean(render(code, with_lib=False))

    def test_doc_mentions_every_public_module(self):
        src = LIB.read_text(encoding="utf-8")
        doc = DOC.read_text(encoding="utf-8")
        names = re.findall(r"^(?:module|function)\s+([a-z][a-z0-9_]*)\s*\(", src, flags=re.M)
        missing = [n for n in names if not re.search(r"\b" + n + r"\(", doc)]
        self.assertEqual(missing, [], "In STUDIO_LIB.md fehlen: " + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
