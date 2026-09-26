"""Tests für scadstudio.imaging (nur unittest + Pillow).

Aufruf: cd scad-studio && python -m unittest discover -s tests
"""

from __future__ import annotations

import io
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont, features  # noqa: E402

from scadstudio import imaging  # noqa: E402

# ---------------------------------------------------------------------------
# Testbilder (werden zur Laufzeit erzeugt, keine Binärdateien im Repo)
# ---------------------------------------------------------------------------

def encode(img: Image.Image, fmt: str = "PNG", **kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def ring_image(size: int = 200) -> Image.Image:
    """Schwarzer Ring (Kreis mit Loch) auf weißem Papier."""
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([size * 0.1, size * 0.1, size * 0.9, size * 0.9], fill="black")
    d.ellipse([size * 0.35, size * 0.35, size * 0.65, size * 0.65], fill="white")
    return img


def star_points(cx: float, cy: float, r_out: float, r_in: float, n: int = 5):
    pts = []
    for k in range(2 * n):
        r = r_out if k % 2 == 0 else r_in
        a = math.pi / 2 + k * math.pi / n
        pts.append((cx + r * math.cos(a), cy - r * math.sin(a)))
    return pts


def big_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return None


def text_image() -> Image.Image:
    """Fetter Text „Ab“ auf weißem Grund."""
    font = big_font(90)
    if font is None:
        small = Image.new("L", (30, 14), 255)
        ImageDraw.Draw(small).text((2, 1), "Ab", fill=0)
        return small.resize((300, 140), Image.NEAREST).convert("RGB")
    img = Image.new("RGB", (300, 140), "white")
    ImageDraw.Draw(img).text((20, 10), "Ab", fill="black", font=font, stroke_width=3, stroke_fill="black")
    return img


def line_art_image() -> Image.Image:
    """Tattoo-artige dünne Linien: Spirale, Schraffur, Stern-Umriss."""
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    pts = []
    for i in range(400):
        a = i * 0.06
        r = 5 + a * 6
        pts.append((200 + r * math.cos(a), 150 + r * math.sin(a)))
    d.line(pts, fill="black", width=2)
    for i in range(15):
        d.line([(10 + i * 8, 10), (20 + i * 8, 70)], fill="black", width=1)
    d.polygon(star_points(330, 220, 60, 25), outline="black")
    return img


def mask_to_polygons_roundtrip(mask):
    return imaging.trace_contours(mask, simplify=0.6, smooth=1)


def segments_intersect(p1, p2, p3, p4) -> bool:
    """Unabhängige Prüfung: schneiden/berühren sich zwei Strecken?"""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    def on_segment(a, b, c):
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])

    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    if o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4):
        return True
    return ((o1 == 0 and on_segment(p1, p2, p3)) or (o2 == 0 and on_segment(p1, p2, p4))
            or (o3 == 0 and on_segment(p3, p4, p1)) or (o4 == 0 and on_segment(p3, p4, p2)))


def assert_no_crossings(testcase: unittest.TestCase, polygons) -> None:
    segs = []
    for pi, poly in enumerate(polygons):
        n = len(poly)
        for si in range(n):
            segs.append((pi, si, n, poly[si], poly[(si + 1) % n]))
    for a in range(len(segs)):
        pa, sa, na, a0, a1 = segs[a]
        ax0, ax1 = min(a0[0], a1[0]), max(a0[0], a1[0])
        ay0, ay1 = min(a0[1], a1[1]), max(a0[1], a1[1])
        for b in range(a + 1, len(segs)):
            pb, sb, nb, b0, b1 = segs[b]
            if pa == pb and abs(sa - sb) in (1, na - 1):
                continue
            if max(b0[0], b1[0]) < ax0 or min(b0[0], b1[0]) > ax1:
                continue
            if max(b0[1], b1[1]) < ay0 or min(b0[1], b1[1]) > ay1:
                continue
            testcase.assertFalse(segments_intersect(a0, a1, b0, b1),
                                 f"Polygon {pa} Kante {sa} schneidet Polygon {pb} Kante {sb}")


# ---------------------------------------------------------------------------
# load_image
# ---------------------------------------------------------------------------

class LoadImageTests(unittest.TestCase):
    def test_formats_return_rgba(self):
        src = ring_image(64)
        formats = ["PNG", "JPEG", "GIF", "BMP"]
        if features.check("webp"):
            formats.append("WEBP")
        for fmt in formats:
            with self.subTest(fmt=fmt):
                img = imaging.load_image(encode(src, fmt))
                self.assertEqual(img.mode, "RGBA")
                self.assertEqual(img.size, (64, 64))

    def test_exif_orientation_applied(self):
        src = Image.new("RGB", (40, 20), "white")
        exif = Image.Exif()
        exif[0x0112] = 6  # 90° im Uhrzeigersinn drehen
        img = imaging.load_image(encode(src, "JPEG", exif=exif))
        self.assertEqual(img.size, (20, 40))

    def test_gif_transparency_becomes_alpha(self):
        src = Image.new("P", (10, 10), 0)
        src.putpalette([255, 255, 255, 0, 0, 0] + [0] * 762)
        src.paste(1, (2, 2, 8, 8))
        img = imaging.load_image(encode(src, "GIF", transparency=0))
        self.assertEqual(img.getpixel((0, 0))[3], 0)
        self.assertEqual(img.getpixel((5, 5)), (0, 0, 0, 255))

    def test_16bit_grayscale_png(self):
        src = Image.new("I;16", (8, 8), 65535)
        img = imaging.load_image(encode(src, "PNG"))
        self.assertEqual(img.getpixel((0, 0))[:3], (255, 255, 255))

    def test_invalid_data_raises_german_value_error(self):
        truncated = encode(ring_image(64), "PNG")[:60]
        for data in (b"", b"das ist kein Bild", truncated):
            with self.subTest(data=data[:10]):
                with self.assertRaises(ValueError) as ctx:
                    imaging.load_image(data)
                self.assertIn("Bild", str(ctx.exception))

    def test_unsupported_format_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            imaging.load_image(encode(ring_image(64), "TIFF"))
        self.assertIn("nicht unterstützt", str(ctx.exception))

    def test_huge_image_is_downscaled(self):
        img = imaging.load_image(encode(Image.new("L", (imaging.MAX_SIDE + 500, 10), 255), "PNG"))
        self.assertLessEqual(max(img.size), imaging.MAX_SIDE)


# ---------------------------------------------------------------------------
# heightmap
# ---------------------------------------------------------------------------

def gradient_image(w: int = 300, h: int = 200, lo: int = 0, hi: int = 255) -> Image.Image:
    img = Image.new("L", (w, h))
    img.putdata([int(round(lo + (hi - lo) * x / (w - 1))) for _ in range(h) for x in range(w)])
    return img.convert("RGBA")


class HeightmapTests(unittest.TestCase):
    def test_size_and_range(self):
        grid = imaging.heightmap(gradient_image(), resolution=150)
        self.assertEqual((len(grid[0]), len(grid)), (150, 100))
        values = [v for row in grid for v in row]
        self.assertGreaterEqual(min(values), 0.0)
        self.assertLessEqual(max(values), 1.0)

    def test_bright_is_high_and_invert(self):
        grid = imaging.heightmap(gradient_image(), resolution=100)
        row = grid[len(grid) // 2]
        self.assertLess(row[0], 0.05)
        self.assertGreater(row[-1], 0.95)
        self.assertTrue(all(a <= b + 1e-9 for a, b in zip(row, row[1:])))
        inv = imaging.heightmap(gradient_image(), resolution=100, invert=True)[len(grid) // 2]
        self.assertGreater(inv[0], 0.95)
        self.assertLess(inv[-1], 0.05)

    def test_row_zero_is_top(self):
        img = Image.new("RGBA", (50, 50), (0, 0, 0, 255))
        img.paste((255, 255, 255, 255), (0, 0, 50, 25))
        grid = imaging.heightmap(img, resolution=50)
        self.assertGreater(grid[0][25], 0.9)
        self.assertLess(grid[-1][25], 0.1)

    def test_auto_contrast_stretches(self):
        img = gradient_image(lo=100, hi=150)
        stretched = imaging.heightmap(img, resolution=60)[10]
        plain = imaging.heightmap(img, resolution=60, auto_contrast=False)[10]
        self.assertLess(stretched[0], 0.05)
        self.assertGreater(stretched[-1], 0.95)
        self.assertGreater(plain[0], 0.35)
        self.assertLess(plain[-1], 0.65)

    def test_gamma(self):
        img = Image.new("RGBA", (10, 10), (128, 128, 128, 255))
        v1 = imaging.heightmap(img, resolution=10, auto_contrast=False)[5][5]
        v2 = imaging.heightmap(img, resolution=10, auto_contrast=False, gamma=2.0)[5][5]
        self.assertAlmostEqual(v2, v1 ** 2, places=3)
        with self.assertRaises(ValueError):
            imaging.heightmap(img, gamma=0)

    def test_transparent_is_background_height_zero(self):
        img = Image.new("RGBA", (40, 40), (255, 255, 255, 0))
        img.paste((255, 255, 255, 255), (10, 10, 30, 30))
        for invert in (False, True):
            grid = imaging.heightmap(img, resolution=40, invert=invert, auto_contrast=False)
            self.assertEqual(grid[0][0], 0.0)
        grid = imaging.heightmap(img, resolution=40, auto_contrast=False)
        self.assertGreater(grid[20][20], 0.95)

    def test_blur_smooths_edges(self):
        img = Image.new("RGBA", (40, 40), (0, 0, 0, 255))
        img.paste((255, 255, 255, 255), (20, 0, 40, 40))
        sharp = imaging.heightmap(img, resolution=40)[20]
        soft = imaging.heightmap(img, resolution=40, blur=2)[20]
        self.assertGreater(max(abs(a - b) for a, b in zip(soft, soft[1:])),
                           0.0)
        self.assertLess(max(abs(a - b) for a, b in zip(soft, soft[1:])),
                        max(abs(a - b) for a, b in zip(sharp, sharp[1:])))

    def test_minimum_two_samples(self):
        grid = imaging.heightmap(Image.new("RGBA", (1000, 1)), resolution=50)
        self.assertEqual(len(grid), 2)
        self.assertEqual(len(grid[0]), 50)


# ---------------------------------------------------------------------------
# binary_mask
# ---------------------------------------------------------------------------

class BinaryMaskTests(unittest.TestCase):
    def test_dark_is_material_and_invert(self):
        mask = imaging.binary_mask(ring_image(200).convert("RGBA"), resolution=100)
        self.assertEqual((len(mask[0]), len(mask)), (100, 100))
        self.assertTrue(mask[50][15])     # Ring
        self.assertFalse(mask[50][50])    # Loch
        self.assertFalse(mask[0][0])      # Papier
        inv = imaging.binary_mask(ring_image(200).convert("RGBA"), resolution=100, invert=True)
        self.assertFalse(inv[50][15])
        self.assertTrue(inv[0][0])

    def test_explicit_threshold(self):
        img = Image.new("RGBA", (20, 20), (100, 100, 100, 255))
        self.assertTrue(imaging.binary_mask(img, resolution=20, threshold=128, cleanup=0)[5][5])
        self.assertFalse(imaging.binary_mask(img, resolution=20, threshold=80, cleanup=0)[5][5])

    def test_alpha_auto_uses_opaque_pixels(self):
        # weißes Logo auf transparentem Grund: nur über Alpha erkennbar
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse([20, 20, 80, 80], fill=(255, 255, 255, 255))
        auto = imaging.binary_mask(img, resolution=100)
        self.assertTrue(auto[50][50])
        self.assertFalse(auto[5][5])
        no_alpha = imaging.binary_mask(img, resolution=100, use_alpha="no")
        self.assertFalse(any(any(row) for row in no_alpha))
        with self.assertRaises(ValueError):
            imaging.binary_mask(img, use_alpha="vielleicht")

    def test_alpha_auto_ignores_tiny_transparency(self):
        img = ring_image(100).convert("RGBA")
        img.putpixel((0, 0), (255, 255, 255, 0))
        mask = imaging.binary_mask(img, resolution=100)
        self.assertFalse(mask[2][2])     # weißes Papier bleibt Hintergrund
        self.assertTrue(mask[50][12])

    def test_cleanup_removes_specks_but_keeps_thin_lines(self):
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        d = ImageDraw.Draw(img)
        d.line([(10, 10), (10, 90)], fill=(0, 0, 0, 255), width=1)   # dünne Linie
        d.point([(50, 20), (70, 30)], fill=(0, 0, 0, 255))            # Staubkörner
        d.rectangle([40, 50, 80, 90], fill=(0, 0, 0, 255))
        d.point([(60, 70)], fill=(255, 255, 255, 255))                # Nadelloch
        clean = imaging.binary_mask(img, resolution=100, threshold=128, cleanup=1)
        self.assertFalse(clean[20][50])
        self.assertFalse(clean[30][70])
        self.assertTrue(clean[70][60])
        self.assertTrue(all(clean[y][10] for y in range(12, 88)))
        raw = imaging.binary_mask(img, resolution=100, threshold=128, cleanup=0)
        self.assertTrue(raw[20][50])
        self.assertFalse(raw[70][60])

    def test_otsu_threshold(self):
        hist = [0] * 256
        hist[30] = 500
        hist[220] = 500
        t = imaging.otsu_threshold(hist)
        self.assertTrue(30 < t <= 220)
        self.assertEqual(imaging.otsu_threshold([0] * 256), 128)


# ---------------------------------------------------------------------------
# trace_contours
# ---------------------------------------------------------------------------

def filled_mask(w, h, x0, y0, x1, y1):
    """Maske mit gefülltem Rechteck; Zeilen y0..y1-1, Spalten x0..x1-1 (Zeile 0 = oben)."""
    return [[x0 <= x < x1 and y0 <= y < y1 for x in range(w)] for y in range(h)]


class TraceContoursTests(unittest.TestCase):
    def test_square_area_and_y_up(self):
        mask = filled_mask(40, 30, 5, 2, 25, 12)  # oben links im Bild
        polys = imaging.trace_contours(mask)
        self.assertEqual(len(polys), 1)
        poly = polys[0]
        # Vereinfachung (0,6 px) darf Ecken minimal anschneiden
        self.assertAlmostEqual(abs(imaging.polygon_area(poly)), 200, delta=20)
        self.assertGreater(imaging.polygon_area(poly), 0)  # außen: gegen den Uhrzeigersinn
        ys = [p[1] for p in poly]
        xs = [p[0] for p in poly]
        # Y nach oben: oberer Bildrand (Zeile 2) liegt bei y = 30 - 2 = 28
        self.assertAlmostEqual(max(ys), 28, delta=0.6)
        self.assertAlmostEqual(min(ys), 18, delta=0.6)
        self.assertAlmostEqual(min(xs), 5, delta=0.6)
        self.assertAlmostEqual(max(xs), 25, delta=0.6)
        self.assertNotEqual(poly[0], poly[-1])

    def test_square_corners_stay_sharp(self):
        polys = imaging.trace_contours(filled_mask(60, 60, 10, 10, 50, 50))
        self.assertLessEqual(len(polys[0]), 12)
        xs = [p[0] for p in polys[0]]
        self.assertAlmostEqual(max(xs) - min(xs), 40, delta=1.0)

    def test_ring_has_hole(self):
        mask = imaging.binary_mask(ring_image(200).convert("RGBA"), resolution=200)
        polys = imaging.trace_contours(mask)
        self.assertEqual(len(polys), 2)
        areas = sorted(imaging.polygon_area(p) for p in polys)
        self.assertLess(areas[0], 0)      # Loch im Uhrzeigersinn
        self.assertGreater(areas[1], 0)
        expected_outer = math.pi * 80.5 ** 2
        self.assertAlmostEqual(areas[1], expected_outer, delta=expected_outer * 0.03)
        self.assertEqual(sorted(imaging.polygon_depths(polys)), [0, 1])
        outer = imaging.outer_only(polys)
        self.assertEqual(len(outer), 1)
        self.assertGreater(imaging.polygon_area(outer[0]), 0)

    def test_island_in_hole_depths(self):
        img = ring_image(200)
        ImageDraw.Draw(img).ellipse([90, 90, 110, 110], fill="black")
        polys = imaging.trace_contours(imaging.binary_mask(img.convert("RGBA"), resolution=200))
        self.assertEqual(sorted(imaging.polygon_depths(polys)), [0, 1, 2])
        self.assertEqual(len(imaging.outer_only(polys)), 1)

    def test_not_mirrored(self):
        # Balken links oben, Punkt rechts unten
        mask = [[False] * 50 for _ in range(40)]
        for y in range(5, 10):
            for x in range(5, 20):
                mask[y][x] = True
        for y in range(30, 36):
            for x in range(40, 46):
                mask[y][x] = True
        polys = imaging.trace_contours(mask)
        centers = sorted((sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
                         for poly in polys)
        (lx, ly), (rx, ry) = centers
        self.assertLess(lx, rx)
        self.assertGreater(ly, ry)  # links = oben im Bild = großes y

    def test_min_area_drops_specks(self):
        mask = filled_mask(20, 20, 2, 2, 12, 12)
        mask[16][16] = True  # Einzelpixel (Fläche 0,5 px²)
        self.assertEqual(len(imaging.trace_contours(mask, min_area=4)), 1)
        self.assertEqual(len(imaging.trace_contours(mask, min_area=0.1)), 2)

    def test_diagonal_pixels_stay_connected(self):
        mask = [[x == y for x in range(30)] for y in range(30)]
        polys = imaging.trace_contours(mask, min_area=1)
        self.assertEqual(len(polys), 1)

    def test_empty_masks(self):
        self.assertEqual(imaging.trace_contours([]), [])
        self.assertEqual(imaging.trace_contours([[False] * 10] * 10), [])

    def test_full_mask_touching_border(self):
        polys = imaging.trace_contours([[True] * 10 for _ in range(8)])
        self.assertEqual(len(polys), 1)
        self.assertAlmostEqual(imaging.polygon_area(polys[0]), 80, delta=10)

    def test_no_crossings_on_complex_drawings(self):
        for name, img in (("linien", line_art_image()), ("text", text_image())):
            with self.subTest(name=name):
                mask = imaging.binary_mask(img.convert("RGBA"), resolution=300)
                polys = imaging.trace_contours(mask, simplify=1.2, smooth=2)
                self.assertGreaterEqual(len(polys), 3)
                assert_no_crossings(self, polys)
                for poly in polys:
                    self.assertGreaterEqual(len(poly), 3)
                    self.assertEqual(len(set(poly)), len(poly))

    def test_simplify_reduces_points(self):
        mask = imaging.binary_mask(ring_image(300).convert("RGBA"), resolution=300)
        fine = sum(len(p) for p in imaging.trace_contours(mask, simplify=0, smooth=0))
        coarse = sum(len(p) for p in imaging.trace_contours(mask, simplify=1.0))
        self.assertLess(coarse, fine / 3)


if __name__ == "__main__":
    unittest.main()
