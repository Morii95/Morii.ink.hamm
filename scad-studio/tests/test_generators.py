"""Tests für scadstudio.generators.

Tests, die OpenSCAD brauchen, werden übersprungen, wenn ``openscad`` nicht
im PATH liegt. Aufruf: cd scad-studio && python -m unittest discover -s tests
"""

from __future__ import annotations

import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from scadstudio import generators, imaging  # noqa: E402

OPENSCAD = shutil.which("openscad")
ALL_MODES = list(generators.SILHOUETTE_MODES)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def ring_polygons():
    """Quadratischer Rahmen: Außenkontur + Loch (Pixelkoordinaten, Y nach oben)."""
    return [square(10, 10, 90, 90), list(reversed(square(30, 30, 70, 70)))]


def figure_polygons():
    """Ring + Stern + fetter Text, wie sie aus einem Bild kommen würden."""
    img = Image.new("RGB", (300, 200), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([10, 10, 110, 110], fill="black")
    d.ellipse([40, 40, 80, 80], fill="white")
    star = []
    for k in range(10):
        r = 45 if k % 2 == 0 else 18
        a = math.pi / 2 + k * math.pi / 5
        star.append((180 + r * math.cos(a), 60 - r * math.sin(a)))
    d.polygon(star, fill="black")
    try:
        font = ImageFont.load_default(size=60)
        d.text((20, 120), "Hi!", fill="black", font=font, stroke_width=2, stroke_fill="black")
    except TypeError:  # Pillow < 10.1: kein skalierbarer Standardfont
        d.rectangle([20, 130, 40, 190], fill="black")
        d.rectangle([60, 130, 75, 190], fill="black")
    mask = imaging.binary_mask(img.convert("RGBA"), resolution=200)
    return imaging.trace_contours(mask), len(mask[0]), len(mask)


def read_stl(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:5] == b"solid" and b"facet" in data[:400]:
        verts = [tuple(float(v) for v in line.split()[1:])
                 for line in data.decode("ascii", "replace").splitlines() if line.strip().startswith("vertex")]
        return [tuple(verts[i:i + 3]) for i in range(0, len(verts) - 2, 3)]
    count = struct.unpack_from("<I", data, 80)[0]
    tris = []
    for i in range(count):
        v = struct.unpack_from("<12f", data, 84 + 50 * i)
        tris.append((v[3:6], v[6:9], v[9:12]))
    return tris


def mesh_stats(tris):
    """Volumen, Bounding Box und Geschlossenheit (jede Kante genau einmal je Richtung)."""
    edges = Counter()
    volume = 0.0
    for a, b, c in tris:
        volume += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
        for p, q in ((a, b), (b, c), (c, a)):
            edges[(p, q)] += 1
    closed = all(n == 1 and edges.get((q, p)) == 1 for (p, q), n in edges.items())
    top = max(v[2] for t in tris for v in t)
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    return {
        "volume": volume,
        "closed": closed,
        "bbox": (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)),
        "top_center_x": _top_center_x(tris, top),
    }


def _top_center_x(tris, top):
    """Flächenschwerpunkt (X) der Deckflächen auf der höchsten Ebene."""
    area_sum = moment = 0.0
    for a, b, c in tris:
        if min(a[2], b[2], c[2]) > top - 1e-4:
            area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
            area_sum += area
            moment += area * (a[0] + b[0] + c[0]) / 3
    return moment / area_sum if area_sum else 0.0


def render(testcase, code, defines=(), timeout=300):
    """Rendert .scad-Code mit OpenSCAD zu STL und prüft auf Warnungen/Fehler."""
    with tempfile.TemporaryDirectory() as tmp:
        scad = os.path.join(tmp, "model.scad")
        stl = os.path.join(tmp, "model.stl")
        with open(scad, "w", encoding="utf-8") as fh:
            fh.write(code)
        cmd = [OPENSCAD, "-o", stl, "--export-format", "binstl"]
        for name, value in defines:
            cmd += ["-D", f"{name}={value}"]
        proc = subprocess.run(cmd + [scad], capture_output=True, text=True, timeout=timeout)
        log = proc.stdout + proc.stderr
        testcase.assertEqual(proc.returncode, 0, log[-2000:])
        problems = [line for line in log.splitlines() if "WARNING" in line or "ERROR" in line]
        testcase.assertEqual(problems, [], log[-2000:])
        tris = read_stl(stl)
    testcase.assertGreater(len(tris), 0)
    stats = mesh_stats(tris)
    testcase.assertTrue(stats["closed"], "STL ist nicht geschlossen")
    testcase.assertGreater(stats["volume"], 0, "Flächen falsch orientiert")
    return stats


def parameter_section(code):
    """Text vor der ersten '{' – nur dort sucht der Customizer von 2021.01 Parameter."""
    return code[:code.index("{")]


# ---------------------------------------------------------------------------
# Hilfsfunktionen für Text
# ---------------------------------------------------------------------------

class TextSafetyTests(unittest.TestCase):
    def test_scad_comment_defuses_comment_markers(self):
        text = generators.scad_comment("Hallo\n*/ module x() {} /* [Hidden] */\r\x00Ende")
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)
        self.assertNotIn("*/", text)
        self.assertNotIn("/*", text)
        self.assertIn("Ende", text)

    def test_scad_string_is_single_safe_literal(self):
        lit = generators.scad_string('Er sagte "Hi"\\\nnächste Zeile\\')
        self.assertTrue(lit.startswith('"') and lit.endswith('"'))
        inner = lit[1:-1]
        self.assertNotIn('"', inner)
        self.assertNotIn("\\", inner)
        self.assertNotIn("\n", inner)
        self.assertIn("nächste Zeile", inner)

    def test_long_titles_are_shortened(self):
        self.assertLessEqual(len(generators.scad_comment("x" * 1000)), 120)


# ---------------------------------------------------------------------------
# relief_scad
# ---------------------------------------------------------------------------

def gradient_grid(cols, rows):
    return [[(c / (cols - 1) + r / (rows - 1)) / 2 for c in range(cols)] for r in range(rows)]


class ReliefScadTests(unittest.TestCase):
    def test_structure(self):
        code = generators.relief_scad(gradient_grid(20, 10), width=80, depth=2.5, base=1.2, frame=3)
        self.assertTrue(code.startswith("// ="))
        self.assertIn("Fenster → Customizer", code)
        params = parameter_section(code)
        for line in ("width = 80; // [10:1:400]", "depth = 2.5; // [0.2:0.1:20]",
                     "base = 1.2; // [0.2:0.1:20]", "frame = 3; // [0:0.5:30]",
                     "/* [Abmessungen] */", "/* [Rahmen] */", "/* [Hidden] */"):
            self.assertIn(line, params)
        self.assertLess(params.index("frame ="), params.index("/* [Hidden] */"))
        self.assertIn("polyhedron(", code)
        self.assertEqual(code.count("\n[0,"), 1)  # erste Zeile beginnt links oben mit 0
        self.assertIn("[0,26,53,79,", code)

    def test_lithophane_labels(self):
        code = generators.relief_scad(gradient_grid(5, 5), mode="lithophane", title="Oma")
        self.assertIn("Lithophanie", code)
        self.assertIn("Mindeststärke", code)
        self.assertIn("// Oma", code)

    def test_file_size_250(self):
        grid = [[((c * 7 + r * 13) % 100) / 99 for c in range(250)] for r in range(250)]
        code = generators.relief_scad(grid)
        self.assertLess(len(code.encode("utf-8")), 400_000)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            generators.relief_scad([[0.5]])
        with self.assertRaises(ValueError):
            generators.relief_scad([[0, 1], [0]])
        with self.assertRaises(ValueError):
            generators.relief_scad(gradient_grid(3, 3), mode="kunst")

    def test_values_are_clamped(self):
        code = generators.relief_scad([[-1, 2], [float("nan"), 0.5]])
        self.assertIn("[0,1000],\n[0,500]", code)

    def test_malicious_title(self):
        code = generators.relief_scad(gradient_grid(3, 3), title='Böse */ } module x() {\n"')
        header = code.split("/* [Abmessungen] */")[0]
        self.assertNotIn("*/", header.replace("/* [", ""))
        self.assertNotIn("{", parameter_section(code).split("/* [Hidden] */")[0])

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_render_flat_volume(self):
        flat = [[0.5] * 11 for _ in range(6)]
        stats = render(self, generators.relief_scad(flat, width=100, depth=3, base=1))
        self.assertAlmostEqual(stats["volume"], 100 * 50 * 2.5, delta=1)
        bbox = stats["bbox"]
        self.assertAlmostEqual(bbox[0], -50, places=3)
        self.assertAlmostEqual(bbox[3], 50, places=3)
        self.assertAlmostEqual(bbox[2], 0, places=3)
        self.assertAlmostEqual(bbox[5], 2.5, places=3)

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_render_frame_size_and_height(self):
        stats = render(self, generators.relief_scad(gradient_grid(30, 20), width=60, depth=2, base=1, frame=4))
        x0, y0, z0, x1, y1, z1 = stats["bbox"]
        self.assertAlmostEqual(x1 - x0, 68, places=2)
        self.assertAlmostEqual(z1, 3, places=3)
        self.assertAlmostEqual(z0, 0, places=3)

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_image_is_upright(self):
        # Obere Bildhälfte hoch, untere tief -> hohe Punkte bei positivem Y
        grid = [[1.0 if r < 5 else 0.0 for _ in range(10)] for r in range(10)]
        code = generators.relief_scad(grid, width=90, depth=5, base=1)
        # Rasterabstand 10 mm: obere Hälfte ab y = +5 mm, untere bis y = -5 mm
        for y0, expected_top in ((5, 6), (-205, 1)):
            probe = code.replace("\nrelief();\n",
                                 f"\nintersection() {{ relief(); translate([-100, {y0}, 0]) cube(200); }}\n")
            stats = render(self, probe)
            self.assertAlmostEqual(stats["bbox"][5], expected_top, places=2)

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_polyhedron_is_manifold_for_cgal(self):
        """3D-Boolesche Operation mit CGAL: scheitert bei nicht geschlossenen Polyedern."""
        grid = gradient_grid(25, 18)
        for frame in (0, 2.5):
            with self.subTest(frame=frame):
                code = generators.relief_scad(grid, width=50, depth=3, base=1, frame=frame)
                cut = render(self, code.replace("\nrelief();\n", "\ndifference() { relief(); cube(5); }\n"))
                full = render(self, code)
                removed = full["volume"] - cut["volume"]
                # Würfel 5 × 5 mm trifft das Relief (Höhe 1..4 mm) vollständig
                self.assertTrue(25 * 1 < removed < 25 * 4, removed)


# ---------------------------------------------------------------------------
# silhouette_scad
# ---------------------------------------------------------------------------

class SilhouetteScadTests(unittest.TestCase):
    def test_mode_dropdown_line(self):
        code = generators.silhouette_scad(ring_polygons(), 100, 100)
        self.assertIn(
            'mode = "extrude"; // [extrude:Flache Figur, plate:Schild mit Relief, '
            "keychain:Schlüsselanhänger, stamp:Stempel, cookie_cutter:Ausstechform, stencil:Schablone]",
            code)

    def test_customizer_layout(self):
        code = generators.silhouette_scad(ring_polygons(), 100, 100, mode="stamp", size=50, height=2, thicken=0.3)
        params = parameter_section(code)
        self.assertIn('mode = "stamp";', params)
        self.assertIn("size = 50; // [5:1:300]", params)
        self.assertIn("thicken = 0.3; // [-3:0.05:3]", params)
        for group in ("Allgemein", "Schild", "Schlüsselanhänger", "Stempel", "Ausstechform", "Schablone", "Hidden"):
            self.assertIn(f"/* [{group}] */", params)
        # alle Customizer-Parameter stehen vor [Hidden], jeder mit Beschreibung darüber
        visible = params.split("/* [Hidden] */")[0]
        lines = visible.splitlines()
        for i, line in enumerate(lines):
            if re.match(r"^\w+ = ", line):
                self.assertTrue(lines[i - 1].startswith("// "), line)
        self.assertIn("function shape_points()", params)

    def test_mode_options(self):
        code = generators.silhouette_scad(
            ring_polygons(), 100, 100, mode="plate",
            plate_margin=7.5, engrave="true", ring_inner="6", unknown_option=5, backing=None,
            stencil_thickness="kaputt", cutter_wall=-5)
        self.assertIn("plate_margin = 7.5;", code)
        self.assertIn("engrave = true;", code)
        self.assertIn("ring_inner = 6;", code)
        self.assertIn("backing = 2;", code)
        self.assertIn("stencil_thickness = 1.2;", code)
        self.assertIn("cutter_wall = 0.4;", code)  # auf Minimum angehoben
        self.assertNotIn("unknown_option", code)

    def test_option_names_documented(self):
        doc = generators.silhouette_scad.__doc__
        for name in generators.SILHOUETTE_OPTION_NAMES:
            self.assertIn(f"``{name}``", doc)

    def test_geometry_data(self):
        code = generators.silhouette_scad(ring_polygons(), 200, 100)
        self.assertIn("img_w = 200;", code)
        self.assertIn("shape_size = [80, 80];", code)
        self.assertIn("[-40,-40],[40,-40],[40,40],[-40,40]", code)  # zentriert
        self.assertIn("function shape_ranges() = [\n    [0,3],[4,7]\n];", code)
        self.assertIn("function outer_ranges() = [\n    [0,3]\n];", code)

    def test_errors(self):
        with self.assertRaises(ValueError):
            generators.silhouette_scad(ring_polygons(), 100, 100, mode="vase")
        with self.assertRaises(ValueError):
            generators.silhouette_scad([], 100, 100)
        with self.assertRaises(ValueError):
            generators.silhouette_scad([[(0, 0), (1, 1)]], 100, 100)

    def test_malicious_title(self):
        code = generators.silhouette_scad(ring_polygons(), 100, 100, title='x\n} */ "; echo("böse")')
        self.assertIn('model_title = "x } * / \'; echo(\'böse\')";', code)

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_even_odd_ring_volume(self):
        code = generators.silhouette_scad(ring_polygons(), 100, 100, size=100, height=2)
        stats = render(self, code)
        self.assertAlmostEqual(stats["volume"], (80 * 80 - 40 * 40) * 2, delta=1)

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_all_modes_render(self):
        polygons, w, h = figure_polygons()
        code = generators.silhouette_scad(polygons, w, h, size=60, title='Test "Ring" */')
        for mode in ALL_MODES:
            with self.subTest(mode=mode):
                stats = render(self, code, [("mode", f'"{mode}"')])
                self.assertAlmostEqual(stats["bbox"][2], 0, places=3)

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_mode_variants_render(self):
        polygons, w, h = figure_polygons()
        cases = [
            ("plate", {"engrave": True}),
            ("keychain", {"raise_height": 0, "backing_solid": False}),
            ("keychain", {"backing": 0}),
            ("stamp", {"handle": True}),
            ("cookie_cutter", {"flange_width": 0}),
            ("extrude", {}),
        ]
        for mode, options in cases:
            with self.subTest(mode=mode, options=options):
                code = generators.silhouette_scad(polygons, w, h, mode=mode, size=50,
                                                  thicken=0.2 if mode == "extrude" else 0, **options)
                render(self, code)

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_orientation_stamp_mirrored_keychain_ring(self):
        # Asymmetrische Figur: hoher Balken links, flacher Balken rechts unten
        polys = [square(0, 0, 20, 100), square(40, 0, 100, 10)]
        flat = render(self, generators.silhouette_scad(polys, 100, 100, mode="extrude", size=100))
        self.assertAlmostEqual(flat["bbox"][0], -50, places=2)
        self.assertLess(flat["top_center_x"], -10)       # nicht gespiegelt
        stamp = render(self, generators.silhouette_scad(polys, 100, 100, mode="stamp", size=100))
        self.assertGreater(stamp["top_center_x"], 10)    # Stempel gespiegelt
        chain = render(self, generators.silhouette_scad(polys, 100, 100, mode="keychain", size=100,
                                                        height=3, backing=2, ring_outer=10))
        self.assertGreater(chain["bbox"][4], 50 + 2 + 5)  # Öse über der Figur
        self.assertAlmostEqual(chain["bbox"][5], 3 + 1, places=2)  # Figur 1 mm erhaben

    @unittest.skipUnless(OPENSCAD, "OpenSCAD nicht installiert")
    def test_cookie_cutter_ignores_holes(self):
        code = generators.silhouette_scad(ring_polygons(), 100, 100, mode="cookie_cutter", size=100,
                                          cutter_wall=1, flange_width=0, cutter_height=10)
        stats = render(self, code)
        # nur die äußere Wand: Umfang 320 mm × 1 mm × 10 mm (+ runde Ecken)
        self.assertAlmostEqual(stats["volume"], (82 * 82 - 80 * 80 - (4 - math.pi)) * 10, delta=20)


if __name__ == "__main__":
    unittest.main()
