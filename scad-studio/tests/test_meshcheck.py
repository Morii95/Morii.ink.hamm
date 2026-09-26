"""Tests für scadstudio.meshcheck (nur unittest aus der Standardbibliothek).

Die meisten Tests schreiben kleine STL-Dateien selbst; die OpenSCAD-Tests
werden übersprungen, wenn kein ``openscad`` im PATH liegt.

Aufruf:  python -m unittest discover -s tests -v   (im Ordner scad-studio)
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scadstudio import meshcheck as mc  # noqa: E402

OPENSCAD = shutil.which("openscad")


# ---------------------------------------------------------------------------
# Hilfsfunktionen: STL-Dateien von Hand erzeugen
# ---------------------------------------------------------------------------

def cube_tris(x0=0.0, y0=0.0, z0=0.0, sx=10.0, sy=10.0, sz=10.0):
    """12 Dreiecke eines Quaders, Normalen nach außen (gegen den Uhrzeigersinn)."""
    x1, y1, z1 = x0 + sx, y0 + sy, z0 + sz
    p000, p100, p110, p010 = (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)
    p001, p101, p111, p011 = (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)
    return [
        (p000, p010, p110), (p000, p110, p100),   # unten  (-z)
        (p001, p101, p111), (p001, p111, p011),   # oben   (+z)
        (p000, p100, p101), (p000, p101, p001),   # vorne  (-y)
        (p010, p011, p111), (p010, p111, p110),   # hinten (+y)
        (p000, p001, p011), (p000, p011, p010),   # links  (-x)
        (p100, p110, p111), (p100, p111, p101),   # rechts (+x)
    ]


def flip(t):
    return (t[0], t[2], t[1])


def normal_of(t):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    ln = math.sqrt(sum(c * c for c in n)) or 1.0
    return tuple(c / ln for c in n)


def write_binary_stl(path, tris, header=b"test", normals=None):
    with open(path, "wb") as fh:
        fh.write(header.ljust(80, b" ")[:80])
        fh.write(struct.pack("<I", len(tris)))
        for i, t in enumerate(tris):
            n = normals[i] if normals is not None else normal_of(t)
            fh.write(struct.pack("<3f", *n))
            for p in t:
                fh.write(struct.pack("<3f", *p))
            fh.write(b"\0\0")


def write_ascii_stl(path, tris):
    lines = ["solid test"]
    for t in tris:
        n = normal_of(t)
        lines.append("  facet normal %g %g %g" % n)
        lines.append("    outer loop")
        for p in t:
            lines.append("      vertex %.6f %.6f %.6f" % p)
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid test")
    Path(path).write_text("\n".join(lines) + "\n")


def codes(result, level=None):
    return {i["code"] for i in result["issues"] if level is None or i["level"] == level}


class _TmpMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="meshcheck-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def stl(self, name, tris, **kw):
        p = self.tmp / name
        write_binary_stl(p, tris, **kw)
        return p


# ---------------------------------------------------------------------------
# Einlesen
# ---------------------------------------------------------------------------

class ReadStlTests(_TmpMixin, unittest.TestCase):
    def test_binary_cube_welded(self):
        m = mc.read_stl(self.stl("c.stl", cube_tris()))
        self.assertEqual(m.triangle_count, 12)
        self.assertEqual(m.vertex_count, 8)
        self.assertEqual(m.fmt, "binary")

    def test_ascii_cube(self):
        p = self.tmp / "a.stl"
        write_ascii_stl(p, cube_tris(sx=20, sy=10, sz=5))
        m = mc.read_stl(p)
        self.assertEqual(m.fmt, "ascii")
        self.assertEqual(m.triangle_count, 12)
        self.assertEqual(m.vertex_count, 8)

    def test_binary_header_starting_with_solid(self):
        # viele CAD-Programme schreiben "solid" in den Binär-Kopf
        m = mc.read_stl(self.stl("s.stl", cube_tris(), header=b"solid binary from CAD"))
        self.assertEqual(m.fmt, "binary")
        self.assertEqual(m.triangle_count, 12)

    def test_near_duplicate_vertices_are_welded(self):
        tris = cube_tris()
        tris[0] = ((0.0, 0.0, 1e-7),) + tris[0][1:]
        m = mc.read_stl(self.stl("w.stl", tris))
        self.assertEqual(m.vertex_count, 8)

    def test_trailing_bytes_tolerated(self):
        p = self.stl("t.stl", cube_tris())
        with open(p, "ab") as fh:
            fh.write(b"\0" * 7)
        m = mc.read_stl(p)
        self.assertEqual(m.triangle_count, 12)
        self.assertTrue(m.notes)

    def test_garbage_raises(self):
        p = self.tmp / "g.stl"
        p.write_bytes(os.urandom(1000))
        with self.assertRaises(ValueError):
            mc.read_stl(p)

    def test_empty_file_raises(self):
        p = self.tmp / "e.stl"
        p.write_bytes(b"")
        with self.assertRaises(ValueError):
            mc.read_stl(p)

    def test_truncated_binary_raises(self):
        p = self.stl("tr.stl", cube_tris())
        data = p.read_bytes()
        p.write_bytes(data[:-80])
        with self.assertRaises(ValueError) as cm:
            mc.read_stl(p)
        self.assertIn("abgeschnitten", str(cm.exception))

    def test_text_that_is_not_stl_raises(self):
        p = self.tmp / "x.stl"
        p.write_text("solid nothing here\nthis is just text\n")
        with self.assertRaises(ValueError):
            mc.read_stl(p)

    def test_ascii_bad_number_raises(self):
        p = self.tmp / "bad.stl"
        p.write_text("solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 abc\nvertex 1 0 0\n"
                     "vertex 0 1 0\nendloop\nendfacet\nendsolid x\n")
        with self.assertRaises(ValueError):
            mc.read_stl(p)

    def test_nan_raises(self):
        tris = cube_tris()
        tris[3] = ((float("nan"), 0.0, 0.0),) + tris[3][1:]
        with self.assertRaises(ValueError):
            mc.read_stl(self.stl("nan.stl", tris))

    def test_stl_bbox(self):
        p = self.stl("b.stl", cube_tris(5, -2, 1, 20, 10, 5))
        bb = mc.stl_bbox(p)
        self.assertEqual(bb["min"], [5.0, -2.0, 1.0])
        self.assertEqual(bb["max"], [25.0, 8.0, 6.0])
        self.assertEqual(bb["size"], [20.0, 10.0, 5.0])
        self.assertEqual(bb["triangles"], 12)

    def test_mesh_from_triangles(self):
        m = mc.Mesh.from_triangles(cube_tris())
        r = mc.analyze(m, wall_samples=100)
        self.assertTrue(r["watertight"])
        self.assertAlmostEqual(r["volume_cm3"], 1.0, places=3)


# ---------------------------------------------------------------------------
# Analyse mit handgemachten Netzen
# ---------------------------------------------------------------------------

class AnalyzeTests(_TmpMixin, unittest.TestCase):
    def test_good_cube(self):
        r = mc.analyze(self.stl("c.stl", cube_tris(sx=20, sy=10, sz=5)))
        self.assertEqual(r["size"], [20.0, 10.0, 5.0])
        self.assertAlmostEqual(r["volume_cm3"], 1.0, places=3)
        self.assertAlmostEqual(r["area_cm2"], 7.0, places=3)
        self.assertTrue(r["watertight"])
        self.assertEqual(r["open_edges"], 0)
        self.assertEqual(r["nonmanifold_edges"], 0)
        self.assertTrue(r["orientation_consistent"])
        self.assertEqual(r["flipped_faces_estimate"], 0)
        self.assertFalse(r["inverted"])
        self.assertEqual(r["shells"], 1)
        self.assertEqual(r["degenerate_triangles"], 0)
        self.assertTrue(r["bed"]["fits"])
        self.assertTrue(r["ok"])
        self.assertEqual([i for i in r["issues"] if i["level"] != "info"], [])
        self.assertAlmostEqual(r["walls"]["min"], 5.0, delta=0.01)
        self.assertEqual(r["walls"]["thin_fraction"], 0.0)
        self.assertAlmostEqual(r["base_area_mm2"], 200.0, places=2)
        json.dumps(r)  # muss JSON-fähig sein

    def test_ascii_equals_binary(self):
        p = self.tmp / "a.stl"
        write_ascii_stl(p, cube_tris(sx=20, sy=10, sz=5))
        ra = mc.analyze(p, wall_samples=200)
        rb = mc.analyze(self.stl("b.stl", cube_tris(sx=20, sy=10, sz=5)), wall_samples=200)
        for key in ("size", "volume_cm3", "area_cm2", "watertight", "shells", "triangles",
                    "vertices", "flipped_faces_estimate"):
            self.assertEqual(ra[key], rb[key], key)

    def test_missing_triangle(self):
        r = mc.analyze(self.stl("m.stl", cube_tris()[:-1]))
        self.assertFalse(r["watertight"])
        self.assertEqual(r["open_edges"], 3)
        self.assertIn("open_edges", codes(r, "error"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["flipped_faces_estimate"], 0)
        self.assertTrue(any("offene Kanten" in i["text"] for i in r["issues"]))

    def test_one_flipped_triangle(self):
        tris = cube_tris()
        tris[5] = flip(tris[5])
        r = mc.analyze(self.stl("f.stl", tris))
        self.assertTrue(r["watertight"])
        self.assertFalse(r["orientation_consistent"])
        self.assertEqual(r["misoriented_edges"], 3)
        self.assertEqual(r["flipped_faces_estimate"], 1)
        self.assertFalse(r["inverted"])
        self.assertIn("flipped", codes(r, "warning"))
        # Volumen wird mit korrigierter Orientierung berechnet
        self.assertAlmostEqual(r["volume_cm3"], 1.0, places=3)
        self.assertAlmostEqual(r["walls"]["min"], 10.0, delta=0.01)

    def test_several_flipped_triangles_majority(self):
        tris = cube_tris()
        for i in (0, 4, 9):
            tris[i] = flip(tris[i])
        r = mc.analyze(self.stl("f3.stl", tris), wall_samples=0)
        self.assertEqual(r["flipped_faces_estimate"], 3)
        self.assertTrue(any(i["text"].startswith("3 Dreiecke falsch herum") for i in r["issues"]))

    def test_inside_out_cube(self):
        r = mc.analyze(self.stl("i.stl", [flip(t) for t in cube_tris()]))
        self.assertTrue(r["inverted"])
        self.assertTrue(r["watertight"])
        self.assertTrue(r["orientation_consistent"])
        self.assertEqual(r["flipped_faces_estimate"], 0)
        self.assertEqual(r["inverted_shells"], 1)
        self.assertLess(r["signed_volume_cm3"], 0)
        self.assertAlmostEqual(r["volume_cm3"], 1.0, places=3)
        self.assertIn("inverted", codes(r, "error"))
        self.assertFalse(r["ok"])
        # Wandstärke trotzdem sinnvoll (mit korrigierten Normalen)
        self.assertAlmostEqual(r["walls"]["min"], 10.0, delta=0.01)

    def test_two_separate_cubes(self):
        tris = cube_tris() + cube_tris(x0=20)
        r = mc.analyze(self.stl("two.stl", tris))
        self.assertEqual(r["shells"], 2)
        self.assertEqual(r["bodies"], 2)
        self.assertEqual(r["cavities"], 0)
        self.assertTrue(r["watertight"])
        self.assertAlmostEqual(r["volume_cm3"], 2.0, places=3)
        self.assertIn("shells", codes(r, "info"))
        self.assertTrue(any("2 getrennten Teilen" in i["text"] for i in r["issues"]))
        self.assertTrue(r["ok"])

    def test_collapsed_triangle_is_degenerate(self):
        tris = cube_tris() + [((0, 0, 0), (0, 0, 0), (10, 0, 0))]
        r = mc.analyze(self.stl("d.stl", tris))
        self.assertEqual(r["triangles"], 13)
        self.assertEqual(r["degenerate_triangles"], 1)
        self.assertTrue(r["watertight"])
        self.assertIn("degenerate", codes(r, "info"))
        self.assertTrue(r["ok"])

    def test_collinear_triangle_is_degenerate(self):
        # T-Stoß: rechte Seite (x=10) hat einen Zusatzpunkt m auf der Unterkante,
        # das Netz wird durch ein Dreieck mit Fläche 0 (a, b, m) geschlossen.
        tris = cube_tris()[:10]
        a, b, c, d = (10, 0, 0), (10, 10, 0), (10, 10, 10), (10, 0, 10)
        m = (10, 5, 0)
        tris += [(a, m, c), (m, b, c), (a, c, d), (a, b, m)]
        r = mc.analyze(self.stl("col.stl", tris), wall_samples=0)
        self.assertEqual(r["degenerate_triangles"], 1)
        self.assertTrue(r["watertight"])
        self.assertTrue(r["orientation_consistent"])
        self.assertAlmostEqual(r["volume_cm3"], 1.0, places=3)

    def test_thin_plate(self):
        r = mc.analyze(self.stl("p.stl", cube_tris(sx=20, sy=20, sz=0.3)))
        w = r["walls"]
        self.assertAlmostEqual(w["min"], 0.3, delta=0.005)
        self.assertGreater(w["thin_fraction"], 0.9)
        self.assertIn("thin_wall", codes(r, "warning"))
        self.assertTrue(r["ok"])  # nur Warnung, kein Fehler
        self.assertTrue(any("Dünnste Wand ca. 0.3 mm" in i["text"] for i in r["issues"]))

    def test_wall_below_recommended(self):
        r = mc.analyze(self.stl("p1.stl", cube_tris(sx=20, sy=20, sz=1.0)))
        self.assertAlmostEqual(r["walls"]["min"], 1.0, delta=0.005)
        self.assertIn("wall_recommended", codes(r, "info"))
        self.assertNotIn("thin_wall", codes(r))

    def test_hollow_box_with_cavity(self):
        outer = cube_tris(0, 0, 0, 30, 30, 30)
        inner = [flip(t) for t in cube_tris(2, 2, 2, 26, 26, 26)]
        r = mc.analyze(self.stl("h.stl", outer + inner))
        self.assertEqual(r["shells"], 2)
        self.assertEqual(r["cavities"], 1)
        self.assertEqual(r["bodies"], 1)
        self.assertEqual(r["inverted_shells"], 0)
        self.assertEqual(r["flipped_faces_estimate"], 0)
        self.assertFalse(r["inverted"])
        self.assertAlmostEqual(r["volume_cm3"], (30 ** 3 - 26 ** 3) / 1000.0, places=3)
        self.assertAlmostEqual(r["walls"]["min"], 2.0, delta=0.01)
        self.assertIn("cavities", codes(r, "info"))
        self.assertTrue(r["ok"])

    def test_cavity_with_wrong_orientation(self):
        outer = cube_tris(0, 0, 0, 30, 30, 30)
        inner = cube_tris(2, 2, 2, 26, 26, 26)     # Hohlraum falsch herum (nach außen)
        r = mc.analyze(self.stl("h2.stl", outer + inner))
        self.assertEqual(r["inverted_shells"], 1)
        self.assertFalse(r["inverted"])
        self.assertIn("inverted_shells", codes(r, "warning"))
        self.assertAlmostEqual(r["volume_cm3"], (30 ** 3 - 26 ** 3) / 1000.0, places=3)
        self.assertAlmostEqual(r["walls"]["min"], 2.0, delta=0.01)

    def test_nonmanifold_edge(self):
        # zwei Würfel, die sich nur an einer Kante berühren
        tris = cube_tris() + cube_tris(x0=10, y0=10)
        r = mc.analyze(self.stl("nm.stl", tris), wall_samples=0)
        self.assertEqual(r["nonmanifold_edges"], 1)
        self.assertFalse(r["watertight"])
        self.assertEqual(r["open_edges"], 0)
        self.assertEqual(r["flipped_faces_estimate"], 0)
        self.assertIn("nonmanifold", codes(r, "warning"))

    def test_stored_normals_mismatch(self):
        tris = cube_tris()
        normals = [tuple(-c for c in normal_of(t)) for t in tris]
        r = mc.analyze(self.stl("n.stl", tris, normals=normals), wall_samples=0)
        self.assertEqual(r["stored_normals_mismatch"], 12)
        self.assertEqual(r["flipped_faces_estimate"], 0)
        self.assertIn("stored_normals", codes(r, "info"))

    def test_zero_normals_are_fine(self):
        tris = cube_tris()
        r = mc.analyze(self.stl("z.stl", tris, normals=[(0, 0, 0)] * 12), wall_samples=0)
        self.assertEqual(r["stored_normals_mismatch"], 0)

    def test_not_on_bed(self):
        r = mc.analyze(self.stl("up.stl", cube_tris(z0=12.3)), wall_samples=0)
        self.assertIn("z0", codes(r, "info"))
        self.assertTrue(any("z=12.3 mm" in i["text"] for i in r["issues"]))

    def test_empty_binary(self):
        r = mc.analyze(self.stl("empty.stl", []))
        self.assertEqual(r["triangles"], 0)
        self.assertFalse(r["ok"])
        self.assertIn("empty", codes(r, "error"))
        json.dumps(r)

    def test_time_budget_partial(self):
        r = mc.analyze(self.stl("tb.stl", cube_tris()), time_budget=0.0)
        self.assertTrue(r["walls"]["partial"])
        self.assertIn("walls_partial", codes(r, "info"))

    def test_no_wall_samples(self):
        r = mc.analyze(self.stl("nw.stl", cube_tris()), wall_samples=0)
        self.assertIsNone(r["walls"])
        self.assertTrue(r["ok"])

    def test_summary_text(self):
        r = mc.analyze(self.stl("s.stl", cube_tris(sx=20, sy=10, sz=5)), wall_samples=100)
        txt = mc.summary_text(r)
        self.assertIn("20 × 10 × 5 mm", txt)
        self.assertIn("1.00 cm³", txt)
        self.assertIn("OK", txt)
        r2 = mc.analyze(self.stl("s2.stl", cube_tris()[:-1]), wall_samples=0)
        self.assertIn("FEHLER", mc.summary_text(r2))


class BedTests(_TmpMixin, unittest.TestCase):
    def test_too_long_fits_when_tilted(self):
        r = mc.analyze(self.stl("l.stl", cube_tris(sx=230, sy=80, sz=40)), wall_samples=0)
        bed = r["bed"]
        self.assertFalse(bed["fits"])
        self.assertTrue(bed["fits_rotated"])
        self.assertIn("gekippt", bed["hint"])
        self.assertIn("bed_rotate", codes(r, "warning"))
        self.assertTrue(any("230 × 80 × 40 mm > 220 × 220 × 250 mm" in i["text"]
                            for i in r["issues"]))

    def test_fits_after_z_rotation(self):
        r = mc.analyze(self.stl("r.stl", cube_tris(sx=100, sy=230, sz=10)), wall_samples=0,
                       bed=(250, 200, 200))
        self.assertFalse(r["bed"]["fits"])
        self.assertTrue(r["bed"]["fits_rotated"])
        self.assertEqual(r["bed"]["orientation"], "rot_z_90")

    def test_fits_diagonally(self):
        # 250 × 20 × 10 mm: passt nur diagonal (bzw. gar nicht, wenn das Bett zu flach ist)
        r = mc.analyze(self.stl("d.stl", cube_tris(sx=250, sy=20, sz=10)), wall_samples=0,
                       bed=(220, 220, 5))
        self.assertFalse(r["bed"]["fits_rotated"])
        r = mc.analyze(self.stl("d2.stl", cube_tris(sx=250, sy=20, sz=10)), wall_samples=0,
                       bed=(220, 220, 100))
        self.assertTrue(r["bed"]["fits_rotated"])
        self.assertEqual(r["bed"]["orientation"], "rot_z")
        self.assertIn("diagonal", r["bed"]["hint"])

    def test_does_not_fit_at_all(self):
        r = mc.analyze(self.stl("big.stl", cube_tris(sx=300, sy=300, sz=300)), wall_samples=0)
        self.assertFalse(r["bed"]["fits"])
        self.assertFalse(r["bed"]["fits_rotated"])
        self.assertIn("bed", codes(r, "error"))
        self.assertFalse(r["ok"])

    def test_custom_bed(self):
        r = mc.analyze(self.stl("c.stl", cube_tris(sx=100, sy=100, sz=100)), wall_samples=0,
                       bed=(90, 90, 90))
        self.assertFalse(r["bed"]["fits_rotated"])
        self.assertEqual(r["bed"]["bed"], [90.0, 90.0, 90.0])


# ---------------------------------------------------------------------------
# Tests mit echten OpenSCAD-Exporten
# ---------------------------------------------------------------------------

@unittest.skipUnless(OPENSCAD, "openscad nicht installiert")
class OpenSCADTests(_TmpMixin, unittest.TestCase):
    def render(self, code, name="m", fmt="binstl"):
        scad = self.tmp / f"{name}.scad"
        out = self.tmp / f"{name}.stl"
        scad.write_text(code)
        proc = subprocess.run([OPENSCAD, "-o", str(out), "--export-format", fmt, str(scad)],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not out.exists():
            self.fail(f"OpenSCAD-Fehler: {proc.stderr[-2000:]}")
        return out

    def test_cube(self):
        r = mc.analyze(self.render("cube([20,10,5]);"))
        self.assertEqual(r["size"], [20.0, 10.0, 5.0])
        self.assertAlmostEqual(r["volume_cm3"], 1.0, places=3)
        self.assertTrue(r["watertight"])
        self.assertEqual(r["shells"], 1)
        self.assertTrue(r["ok"])

    def test_cube_ascii(self):
        r = mc.analyze(self.render("cube([20,10,5]);", fmt="asciistl"))
        self.assertEqual(r["format"], "ascii")
        self.assertEqual(r["size"], [20.0, 10.0, 5.0])
        self.assertAlmostEqual(r["volume_cm3"], 1.0, places=3)
        self.assertTrue(r["watertight"])

    def test_hollow_box_wall(self):
        r = mc.analyze(self.render("difference(){ cube(30); translate([2,2,2]) cube(26); }"))
        self.assertAlmostEqual(r["walls"]["min"], 2.0, delta=0.05)
        self.assertTrue(r["watertight"])
        self.assertEqual(r["cavities"], 1)
        self.assertAlmostEqual(r["volume_cm3"], 9.424, places=2)

    def test_open_box_thin_wall(self):
        r = mc.analyze(self.render(
            "difference(){ cube([20,20,10]); translate([0.5,0.5,0.5]) cube([19,19,10]); }"))
        self.assertAlmostEqual(r["walls"]["min"], 0.5, delta=0.02)
        self.assertIn("thin_wall", codes(r, "warning"))
        self.assertTrue(r["watertight"])

    def test_sphere_volume(self):
        r = mc.analyze(self.render("sphere(r=10, $fn=96);"), wall_samples=300)
        exact = 4.0 / 3.0 * math.pi * 1000 / 1000.0
        self.assertAlmostEqual(r["volume_cm3"], exact, delta=exact * 0.01)
        self.assertTrue(r["watertight"])
        self.assertAlmostEqual(r["walls"]["median"], 20.0, delta=0.1)

    def test_two_disjoint_cubes(self):
        r = mc.analyze(self.render("cube(10); translate([20,0,0]) cube(10);"))
        self.assertEqual(r["shells"], 2)
        self.assertTrue(r["watertight"])

    def test_polyhedron_with_reversed_face(self):
        # Klassischer OpenSCAD-Fehler: eine Fläche falsch herum angegeben
        code = """
        polyhedron(
          points=[[0,0,0],[10,0,0],[10,10,0],[0,10,0],[0,0,10],[10,0,10],[10,10,10],[0,10,10]],
          faces=[[0,1,2,3],[4,5,1,0],[7,6,5,4],[5,6,2,1],[6,7,3,2],[3,7,4,0]]);
        """
        r = mc.analyze(self.render(code), wall_samples=0)
        self.assertTrue(r["watertight"])
        self.assertTrue(r["orientation_consistent"])
        good_vol = r["volume_cm3"]
        self.assertAlmostEqual(good_vol, 1.0, places=3)
        bad = code.replace("[6,7,3,2]", "[2,3,7,6]")
        r2 = mc.analyze(self.render(bad, "bad"), wall_samples=0)
        self.assertFalse(r2["orientation_consistent"])
        self.assertEqual(r2["flipped_faces_estimate"], 2)   # Viereck = 2 Dreiecke

    def test_performance_medium(self):
        p = self.render("sphere(r=30, $fn=250);", "perf")
        t = time.perf_counter()
        r = mc.analyze(p)
        dt = time.perf_counter() - t
        self.assertGreater(r["triangles"], 60000)
        self.assertTrue(r["watertight"])
        self.assertLess(dt, 15.0)
        self.assertEqual(r["walls"]["samples"], 2000)


if __name__ == "__main__":
    unittest.main()
