"""Bildvorverarbeitung für SCAD Studio.

Wandelt hochgeladene Bilder (z. B. aus Google Gemini, Tattoo-Linienzeichnungen,
Logos) in Daten um, aus denen ``generators`` OpenSCAD-Code erzeugt:

* :func:`heightmap` – Höhenraster (0..1) für Reliefs und Lithophanien
* :func:`binary_mask` – Schwarz-Weiß-Maske (True = Material)
* :func:`trace_contours` – geschlossene Konturpolygone der Maske

Nur Standardbibliothek + Pillow, lauffähig ab Python 3.9.

Koordinaten:

* ``heightmap()`` und ``binary_mask()`` liefern Zeilen von oben nach unten
  (Zeile 0 = oberste Bildzeile).
* ``trace_contours()`` liefert Pixelkoordinaten mit Y nach OBEN; das Bild
  belegt den Bereich 0..Breite × 0..Höhe. So erscheint die Form in OpenSCAD
  (Blick von oben) nicht gespiegelt.
"""

from __future__ import annotations

import io
import math
import re
from collections import defaultdict
from collections.abc import Sequence

from PIL import Image, ImageChops, ImageFilter, ImageOps

__all__ = [
    "load_image",
    "heightmap",
    "binary_mask",
    "trace_contours",
    "outer_only",
    "polygon_area",
    "polygon_depths",
    "otsu_threshold",
]

Point = tuple[float, float]
Polygon = list[Point]

#: Erlaubte Formate (Pillow-Namen). Andere Formate werden bewusst abgelehnt,
#: z. B. EPS, das Pillow über Ghostscript öffnen würde.
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "MPO", "WEBP", "GIF", "BMP"})
#: Obergrenze für die Pixelzahl des Originalbilds.
MAX_PIXELS = 80_000_000
#: Größere Bilder werden beim Laden auf diese Kantenlänge verkleinert.
MAX_SIDE = 4096

try:  # Pillow >= 9.1
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - ältere Pillow-Versionen
    _LANCZOS = Image.LANCZOS

# Nachkommastellen der Konturkoordinaten (Pixel). 0,01 px ist weit unter
# der Druckauflösung und hält die .scad-Dateien klein.
_COORD_DECIMALS = 2


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------

def load_image(data: bytes) -> Image.Image:
    """Dekodiert PNG/JPEG/WebP/GIF/BMP-Bytes und liefert ein RGBA-Bild.

    Die EXIF-Ausrichtung (Handyfotos) wird angewendet, bei animierten GIF/WebP
    wird das erste Bild verwendet. Sehr große Bilder werden auf eine
    Kantenlänge von höchstens ``MAX_SIDE`` Pixeln verkleinert.

    Raises:
        ValueError: mit deutscher Fehlermeldung, wenn die Daten kein
            lesbares Bild in einem unterstützten Format sind.
    """
    if not data:
        raise ValueError("Die Bilddatei ist leer.")
    try:
        img = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as exc:
        raise ValueError(
            f"Das Bild ist zu groß (höchstens {MAX_PIXELS // 1_000_000} Megapixel)."
        ) from exc
    except Exception as exc:
        raise ValueError(
            "Die Datei konnte nicht als Bild gelesen werden. "
            "Unterstützt werden PNG, JPEG, WebP, GIF und BMP."
        ) from exc

    fmt = (img.format or "").upper()
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(
            f"Das Bildformat „{fmt or 'unbekannt'}“ wird nicht unterstützt. "
            "Bitte PNG, JPEG, WebP, GIF oder BMP verwenden."
        )
    width, height = img.size
    if width < 1 or height < 1:
        raise ValueError("Das Bild hat keine gültige Größe.")
    if width * height > MAX_PIXELS:
        raise ValueError(
            f"Das Bild ist mit {width}×{height} Pixeln zu groß "
            f"(höchstens {MAX_PIXELS // 1_000_000} Megapixel)."
        )

    try:
        if fmt in ("JPEG", "MPO") and max(width, height) > MAX_SIDE:
            # JPEG kann beim Dekodieren direkt verkleinert werden (schneller).
            img.draft("RGB", (MAX_SIDE, MAX_SIDE))
        img.load()
    except Exception as exc:
        raise ValueError(
            "Das Bild ist beschädigt oder unvollständig und kann nicht gelesen werden."
        ) from exc

    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass  # defekte EXIF-Daten: Bild unverändert verwenden

    try:
        img = _to_rgba(img)
    except Exception as exc:
        raise ValueError("Das Bild verwendet einen nicht unterstützten Farbmodus.") from exc

    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), _LANCZOS)
    return img


def _to_rgba(img: Image.Image) -> Image.Image:
    """Wandelt ein beliebiges Pillow-Bild nach RGBA um (16-Bit-Graustufen korrekt skaliert)."""
    if img.mode == "RGBA":
        return img
    if img.mode.startswith("I"):
        # 16/32-Bit-Graustufen (z. B. 16-Bit-PNG): auf 8 Bit herunterrechnen
        img = img.convert("I")
        if img.getextrema()[1] > 255:
            img = img.point(lambda v: v * (1 / 256.0))
        img = img.convert("L")
    elif img.mode == "F":
        img = img.convert("L")
    return img.convert("RGBA")


def _ensure_rgba(img: Image.Image) -> Image.Image:
    if not isinstance(img, Image.Image):
        raise TypeError("Erwartet ein PIL-Bild (z. B. aus load_image()).")
    return _to_rgba(img)


def _target_size(width: int, height: int, resolution: int) -> tuple[int, int]:
    """Größe, bei der die längere Seite ``resolution`` Pixel hat (mindestens 2)."""
    res = max(2, int(resolution))
    if width >= height:
        return res, max(2, int(round(height * res / width)))
    return max(2, int(round(width * res / height))), res


def _resized(img: Image.Image, resolution: int) -> Image.Image:
    rgba = _ensure_rgba(img)
    size = _target_size(rgba.width, rgba.height, resolution)
    if size == rgba.size:
        return rgba
    # Pillow rechnet RGBA beim Skalieren mit vormultipliziertem Alpha,
    # transparente Pixel färben die Ränder also nicht ein.
    return rgba.resize(size, _LANCZOS)


# ---------------------------------------------------------------------------
# Höhenraster
# ---------------------------------------------------------------------------

def heightmap(
    img: Image.Image,
    *,
    resolution: int = 200,
    invert: bool = False,
    blur: float = 0.0,
    gamma: float = 1.0,
    auto_contrast: bool = True,
) -> list[list[float]]:
    """Graustufen-Höhenraster mit Werten 0..1 (Zeile 0 = oberste Bildzeile).

    Die längere Bildseite wird auf ``resolution`` Stützstellen skaliert
    (Seitenverhältnis bleibt, mindestens 2).

    * Standard: hell = hoch (1.0), dunkel = tief (0.0).
    * ``invert=True``: dunkel = hoch (für Lithophanien: dunkel = dick).
    * ``auto_contrast``: streckt die Helligkeiten der deckenden Pixel auf den
      vollen Bereich (je 0,5 % Ausreißer an beiden Enden werden ignoriert).
    * ``gamma``: Wert v wird zu v ** gamma (> 1 betont hohe, < 1 tiefe Bereiche).
    * ``blur``: Weichzeichner-Radius in Rasterpunkten (0 = aus).

    Transparenz: Transparente Pixel gelten immer als Hintergrund und bekommen
    die Höhe 0 – unabhängig von ``invert``. Halbtransparente Pixel werden
    anteilig abgesenkt (Höhe × Deckkraft), so laufen Kanten weich aus. Beim
    Relief liegt ein freigestelltes Motiv damit erhaben auf der Grundplatte,
    bei der Lithophanie ist transparenter Hintergrund dünn (also hell, wie
    weißes Papier).
    """
    if gamma <= 0:
        raise ValueError("gamma muss größer als 0 sein.")
    small = _resized(img, resolution)
    width, height = small.size
    gray = small.convert("RGB").convert("L")
    alpha = small.getchannel("A")

    lut = list(range(256))
    if auto_contrast:
        opaque = alpha.point(lambda a: 255 if a >= 128 else 0)
        lo, hi = _histogram_range(gray.histogram(mask=opaque), cutoff=0.005)
        if hi > lo:
            lut = [_clamp_byte((v - lo) * 255.0 / (hi - lo)) for v in range(256)]
    if invert:
        lut = [255 - v for v in lut]

    level = gray.point(lut)
    level = ImageChops.multiply(level, alpha)  # transparent -> Höhe 0
    if blur and blur > 0:
        level = level.filter(ImageFilter.GaussianBlur(float(blur)))

    table = [round((v / 255.0) ** gamma, 4) for v in range(256)]
    raw = level.tobytes()
    return [[table[v] for v in raw[r * width:(r + 1) * width]] for r in range(height)]


def _clamp_byte(value: float) -> int:
    return 0 if value < 0 else 255 if value > 255 else int(round(value))


def _histogram_range(hist: Sequence[int], cutoff: float) -> tuple[int, int]:
    """Kleinster/größter Grauwert, nachdem je ``cutoff`` Anteil Ausreißer abgeschnitten wurden."""
    total = sum(hist)
    if total == 0:
        return 0, 255
    limit = total * cutoff
    lo, acc = 0, 0
    for value, count in enumerate(hist):
        acc += count
        if acc > limit:
            lo = value
            break
    hi, acc = 255, 0
    for value in range(255, -1, -1):
        acc += hist[value]
        if acc > limit:
            hi = value
            break
    return lo, hi


# ---------------------------------------------------------------------------
# Schwarz-Weiß-Maske
# ---------------------------------------------------------------------------

def otsu_threshold(hist: Sequence[int]) -> int:
    """Automatischer Schwellwert nach Otsu für ein 256er-Histogramm.

    Ergebnis t: Werte ``< t`` bilden die dunkle Klasse. Bei mehreren gleich
    guten Schwellen wird die mittlere genommen (robust bei reinem Schwarz-Weiß).
    """
    total = sum(hist)
    if total == 0:
        return 128
    sum_all = sum(i * c for i, c in enumerate(hist))
    weight_dark = 0
    sum_dark = 0.0
    best = -1.0
    best_first = best_last = 127
    for t in range(255):  # dunkle Klasse = 0..t
        weight_dark += hist[t]
        sum_dark += t * hist[t]
        weight_light = total - weight_dark
        if weight_dark == 0 or weight_light == 0:
            continue
        mean_dark = sum_dark / weight_dark
        mean_light = (sum_all - sum_dark) / weight_light
        between = weight_dark * weight_light * (mean_dark - mean_light) ** 2
        if between > best * (1 + 1e-12):
            best = between
            best_first = best_last = t
        elif between >= best * (1 - 1e-12):
            best_last = t
    if best < 0:  # einfarbiges Bild
        return 128
    return (best_first + best_last) // 2 + 1


def binary_mask(
    img: Image.Image,
    *,
    resolution: int = 400,
    threshold: int | None = None,
    invert: bool = False,
    use_alpha: str = "auto",
    blur: float = 0.0,
    cleanup: int = 1,
) -> list[list[bool]]:
    """Schwarz-Weiß-Maske des Bildes, ``True`` = Material. Zeile 0 = oben.

    Die längere Bildseite wird auf ``resolution`` Pixel skaliert (vor dem
    Schwellwert, damit Kanten auch bei kleinen Bildern glatt bleiben).

    * Standard: DUNKLE Pixel sind Material (schwarze Zeichnung auf weißem
      Papier). Transparente Pixel zählen dabei als weißes Papier.
    * ``threshold``: Pixel mit Helligkeit ``< threshold`` (0..255) gelten als
      dunkel; ``None`` = automatisch nach Otsu.
    * ``use_alpha``: ``'auto'`` nutzt die Deckkraft, wenn das Bild nennenswert
      Transparenz enthält (mindestens 2 % transparente und 0,5 % deckende
      Pixel), ``'yes'`` immer, ``'no'`` nie. Im Alpha-Modus sind DECKENDE
      Pixel Material (freigestelltes Logo = Form, auch wenn es weiß ist);
      ``threshold`` bezieht sich dann auf die Deckkraft (Standard 128).
    * ``invert``: vertauscht Material und Hintergrund.
    * ``blur``: Weichzeichner-Radius in Pixeln vor dem Schwellwert.
    * ``cleanup``: Radius r in Pixeln für die Fleckentfernung (0 = aus).
      Materialinseln und Löcher mit weniger als (2r+1)² Pixeln werden
      entfernt bzw. gefüllt. Anders als ein morphologisches Öffnen/Schließen
      bleiben dabei dünne, lange Linien (Tattoo-Linienzeichnungen) erhalten
      und nahe beieinander liegende Linien verschmelzen nicht.
    """
    if use_alpha not in ("auto", "yes", "no"):
        raise ValueError("use_alpha muss 'auto', 'yes' oder 'no' sein.")
    if threshold is not None and not 0 <= int(threshold) <= 256:
        raise ValueError("threshold muss zwischen 0 und 255 liegen.")

    small = _resized(img, resolution)
    width, height = small.size
    alpha = small.getchannel("A")
    alpha_mode = use_alpha == "yes" or (use_alpha == "auto" and _has_transparency(alpha))

    if alpha_mode:
        source = alpha
        if blur and blur > 0:
            source = source.filter(ImageFilter.GaussianBlur(float(blur)))
        t = 128 if threshold is None else int(threshold)
        table = bytes(1 if v >= t else 0 for v in range(256))
    else:
        paper = Image.new("RGBA", small.size, (255, 255, 255, 255))
        source = Image.alpha_composite(paper, small).convert("L")
        if blur and blur > 0:
            source = source.filter(ImageFilter.GaussianBlur(float(blur)))
        t = otsu_threshold(source.histogram()) if threshold is None else int(threshold)
        table = bytes(1 if v < t else 0 for v in range(256))
    if invert:
        table = bytes(1 - b for b in table)

    bits = source.tobytes().translate(table)
    rows = [bytearray(bits[r * width:(r + 1) * width]) for r in range(height)]
    if cleanup and cleanup > 0:
        min_area = (2 * int(cleanup) + 1) ** 2
        _remove_small_regions(rows, material=True, min_area=min_area)
        _remove_small_regions(rows, material=False, min_area=min_area)
    return [list(map(bool, row)) for row in rows]


def _has_transparency(alpha: Image.Image) -> bool:
    """True, wenn die Transparenz des Bildes offensichtlich Motiv und Hintergrund trennt."""
    hist = alpha.histogram()
    total = sum(hist)
    transparent = sum(hist[:128])
    return transparent >= 0.02 * total and (total - transparent) >= 0.005 * total


def _remove_small_regions(rows: list[bytearray], *, material: bool, min_area: int) -> None:
    """Entfernt zusammenhängende Gebiete mit weniger als ``min_area`` Pixeln (in place).

    ``material=True``: Materialinseln (8er-Nachbarschaft) werden gelöscht.
    ``material=False``: Löcher (4er-Nachbarschaft) werden gefüllt; Hintergrund,
    der den Bildrand berührt, ist kein Loch und bleibt unverändert.
    Arbeitet auf Lauflängen (Runs) mit Union-Find und ist daher auch in
    reinem Python schnell.
    """
    value = 1 if material else 0
    pattern = re.compile(b"\x01+" if material else b"\x00+")
    diagonal = material  # passt zur Sattelpunkt-Regel in trace_contours()
    height = len(rows)
    if height == 0:
        return
    width = len(rows[0])

    runs: list[tuple[int, int, int]] = []  # (zeile, start, ende)
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    prev: list[int] = []
    for y, row in enumerate(rows):
        cur: list[int] = []
        k = 0
        for match in pattern.finditer(row):
            start, end = match.span()
            idx = len(runs)
            runs.append((y, start, end))
            parent.append(idx)
            lo = start - 1 if diagonal else start
            hi = end + 1 if diagonal else end
            # Läufe der Vorzeile sind sortiert: Zeiger k wandert nur vorwärts
            while k < len(prev) and runs[prev[k]][2] <= lo:
                k += 1
            m = k
            while m < len(prev) and runs[prev[m]][1] < hi:
                ra, rb = find(idx), find(prev[m])
                if ra != rb:
                    parent[ra] = rb
                m += 1
            cur.append(idx)
        prev = cur

    area: dict = defaultdict(int)
    touches_border: set = set()
    for idx, (y, start, end) in enumerate(runs):
        root = find(idx)
        area[root] += end - start
        if y == 0 or y == height - 1 or start == 0 or end == width:
            touches_border.add(root)

    fill = bytes([1 - value])
    for idx, (y, start, end) in enumerate(runs):
        root = find(idx)
        if area[root] >= min_area:
            continue
        if not material and root in touches_border:
            continue
        rows[y][start:end] = fill * (end - start)


# ---------------------------------------------------------------------------
# Konturverfolgung
# ---------------------------------------------------------------------------

# Marching Squares: Ecken-Bits TL=8, TR=4, BR=2, BL=1. In der Tabelle sind
# die Segmente so gerichtet, dass Material in Bildkoordinaten (Y nach unten)
# rechts liegt; beim Verketten werden sie umgedreht. Nach der Y-Spiegelung
# liegt Material dann links: Außenkonturen laufen gegen den Uhrzeigersinn,
# Löcher im Uhrzeigersinn. Sattelpunkte (5, 10) werden als
# verbunden behandelt: diagonal benachbarte Pixel einer dünnen Linie bleiben
# zusammen (8er-Nachbarschaft für Material).
_CASE_SEGMENTS = {
    1: (("L", "B"),),
    2: (("B", "R"),),
    3: (("L", "R"),),
    4: (("R", "T"),),
    5: (("L", "T"), ("R", "B")),
    6: (("B", "T"),),
    7: (("L", "T"),),
    8: (("T", "L"),),
    9: (("T", "B"),),
    10: (("T", "R"), ("B", "L")),
    11: (("T", "R"),),
    12: (("R", "L"),),
    13: (("R", "B"),),
    14: (("B", "L"),),
}

_TO_ASCII_BITS = bytes.maketrans(b"\x00\x01", b"01")


def _marching_squares(rows: list[bytes], width: int, height: int) -> list[Polygon]:
    """Rohkonturen einer 0/1-Maske (Kantenmittelpunkte, Y nach oben)."""
    w2 = width + 2  # Breite mit leerem Rand
    # Konturpunkte liegen auf halbem Weg zwischen zwei Pixelmitten
    # (Indizes im gepolsterten Raster): Schlüssel 2*(i*w2 + j) = zwischen
    # (i, j) und (i, j+1), Schlüssel 2*(i*w2 + j) + 1 = zwischen (i, j) und (i+1, j).
    offsets = {"T": 0, "B": 2 * w2, "L": 1, "R": 3}
    table = {
        case: tuple((offsets[a], offsets[b]) for a, b in segs)
        for case, segs in _CASE_SEGMENTS.items()
    }

    empty = bytes(w2)
    padded = [empty] + [b"\x00" + r + b"\x00" for r in rows] + [empty]
    # Jede Zeile zusätzlich als Bitmaske (Bit j = Pixel j), um gleichförmige
    # Bereiche schnell zu überspringen.
    masks = [int(r.translate(_TO_ASCII_BITS)[::-1], 2) for r in padded]
    cell_mask = (1 << (width + 1)) - 1

    nxt = {}
    for i in range(height + 1):
        a, b = masks[i], masks[i + 1]
        mixed = ((a ^ (a >> 1)) | (b ^ (b >> 1)) | (a ^ b)) & cell_mask
        if not mixed:
            continue
        top, bottom = padded[i], padded[i + 1]
        base = 2 * i * w2
        while mixed:
            low = mixed & -mixed
            j = low.bit_length() - 1
            mixed ^= low
            case = (top[j] << 3) | (top[j + 1] << 2) | (bottom[j + 1] << 1) | bottom[j]
            key = base + 2 * j
            for src, dst in table[case]:
                nxt[key + dst] = key + src  # umgekehrt: Material links (Y nach oben)

    loops: list[Polygon] = []
    while nxt:
        start, cur = nxt.popitem()
        keys = [start]
        while cur != start:
            keys.append(cur)
            cur = nxt.pop(cur)
        loop = []
        for k in keys:
            idx, vertical = k >> 1, k & 1
            i, j = divmod(idx, w2)
            if vertical:
                loop.append((j - 0.5, float(height - i)))
            else:
                loop.append((float(j), height - i + 0.5))
        loops.append(loop)
    return loops


def polygon_area(poly: Sequence[Point]) -> float:
    """Vorzeichenbehaftete Fläche (positiv = gegen den Uhrzeigersinn bei Y nach oben)."""
    n = len(poly)
    s = 0.0
    for i in range(n):
        x0, y0 = poly[i - 1]
        x1, y1 = poly[i]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def _remove_collinear(pts: Sequence[Point]) -> Polygon:
    n = len(pts)
    out = []
    for i in range(n):
        px, py = pts[i - 1]
        x, y = pts[i]
        qx, qy = pts[(i + 1) % n]
        if (x - px) * (qy - y) - (y - py) * (qx - x) != 0:
            out.append(pts[i])
    return out


def _dp_open(pts: Sequence[Point], eps: float) -> Polygon:
    """Douglas-Peucker für einen offenen Linienzug (iterativ, ohne Rekursion)."""
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = bytearray(n)
    keep[0] = keep[-1] = 1
    eps2 = eps * eps
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        if e - s < 2:
            continue
        ax, ay = pts[s]
        bx, by = pts[e]
        dx, dy = bx - ax, by - ay
        len2 = dx * dx + dy * dy
        best, best_i = -1.0, -1
        for i in range(s + 1, e):
            px, py = pts[i]
            if len2 == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / len2
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, best_i = d, i
        if best > eps2:
            keep[best_i] = 1
            stack.append((s, best_i))
            stack.append((best_i, e))
    return [p for p, k in zip(pts, keep) if k]


def _dp_closed(pts: Sequence[Point], eps: float) -> Polygon:
    """Douglas-Peucker für geschlossene Polygone (zwei Anker: Punkt 0 und der entfernteste)."""
    n = len(pts)
    if n <= 4:
        return list(pts)
    x0, y0 = pts[0]
    far = max(range(n), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
    first = _dp_open(pts[:far + 1], eps)
    second = _dp_open(list(pts[far:]) + [pts[0]], eps)
    return first[:-1] + second[:-1]


def _chaikin_closed(pts: Sequence[Point], iterations: int) -> Polygon:
    """Chaikin-Glättung eines geschlossenen Polygons (Schnitte bei 1/4 und 3/4 jeder Kante).

    Wird auf die Rohkontur angewendet, deren Kanten höchstens etwa 1 px lang
    sind: Treppenstufen verschwinden, echte Ecken runden sich nur um
    Bruchteile eines Pixels ab.
    """
    pts = list(pts)
    for _ in range(iterations):
        n = len(pts)
        if n < 3:
            break
        out = []
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        pts = out
    return pts


def _round_polygon(pts: Sequence[Point]) -> Polygon:
    out: Polygon = []
    for x, y in pts:
        p = (round(x, _COORD_DECIMALS) + 0.0, round(y, _COORD_DECIMALS) + 0.0)
        if not out or p != out[-1]:
            out.append(p)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _process_loop(raw: Polygon, raw_area: float, eps: float, smooth: int) -> Polygon | None:
    """Glättet und vereinfacht eine Rohkontur; None, wenn sie dabei entartet.

    Reihenfolge: erst Chaikin auf der feinen Rohkontur, dann Douglas-Peucker.
    So liegen die verbleibenden Eckpunkte auf einer geglätteten Kurve, und die
    Punktzahl bleibt klein – wichtig für kurze Renderzeiten in OpenSCAD 2021
    (CGAL-Operationen werden mit der Punktzahl überproportional langsamer).
    """
    pts = _chaikin_closed(raw, smooth) if smooth > 0 else raw
    pts = _dp_closed(pts, eps) if eps > 0 else _remove_collinear(pts)
    pts = _round_polygon(pts)
    if len(pts) < 3 or abs(polygon_area(pts)) < 0.25 * abs(raw_area):
        return None
    return pts


def trace_contours(
    mask: Sequence[Sequence[bool]],
    *,
    simplify: float = 0.6,
    smooth: int = 1,
    min_area: float = 4.0,
) -> list[Polygon]:
    """Konturen der Maske als geschlossene Polygone (Marching Squares).

    Rückgabe: Liste von Polygonen (Punktlisten ohne wiederholten Startpunkt)
    in Pixelkoordinaten mit Y nach OBEN; das Bild belegt 0..Breite × 0..Höhe.
    Enthalten sind Außenkonturen UND Löcher (auch Inseln in Löchern); gedacht
    für Even-Odd-Füllung. Außenkonturen sind gegen den Uhrzeigersinn
    orientiert, Löcher im Uhrzeigersinn.

    * ``simplify``: Douglas-Peucker-Toleranz in Pixeln (0 = nur exakt
      kollineare Punkte entfernen).
    * ``smooth``: Anzahl Chaikin-Glättungsdurchläufe (0 = aus). Geglättet
      wird die feine Rohkontur VOR dem Vereinfachen; das entfernt die
      Pixeltreppen, ohne die Punktzahl zu erhöhen.
    * ``min_area``: Konturen mit einer Fläche unter ``min_area`` px² (gemessen
      an der Rohkontur) entfallen. Ein Loch ist immer kleiner als seine
      Außenkontur, fällt also nie ohne sie weg.

    Garantie: Die Polygone schneiden oder berühren sich nicht (auch nicht
    selbst). Führt Vereinfachen/Glätten an einer engen Stelle zu einer
    Überschneidung, werden die betroffenen Konturen schrittweise feiner
    berechnet, notfalls als unveränderte Rohkontur.
    """
    height = len(mask)
    width = len(mask[0]) if height else 0
    if height == 0 or width == 0:
        return []
    rows = [bytes(map(bool, row)) for row in mask]
    if any(len(r) != width for r in rows):
        raise ValueError("Die Maske muss rechteckig sein (alle Zeilen gleich lang).")

    simplify = max(0.0, float(simplify))
    smooth = max(0, int(smooth))
    raw_loops = []
    for loop in _marching_squares(rows, width, height):
        area = polygon_area(loop)
        if abs(area) >= min_area:
            raw_loops.append((loop, area))
    if not raw_loops:
        return []

    # Qualitätsstufen: bei Überschneidungen wird zur nächsten gewechselt,
    # die letzte (unveränderte Rohkontur) kann sich nie überschneiden.
    levels: list[tuple[float, int]] = []
    for level in ((simplify, smooth), (simplify / 2, smooth), (simplify / 4, 0), (0.0, 0)):
        if level not in levels:
            levels.append(level)

    level_of = [0] * len(raw_loops)
    result: list[Polygon | None] = [None] * len(raw_loops)
    todo = range(len(raw_loops))
    while True:
        for idx in todo:
            loop, area = raw_loops[idx]
            while True:
                eps, sm = levels[level_of[idx]]
                pts = _process_loop(loop, area, eps, sm)
                if pts is not None or level_of[idx] == len(levels) - 1:
                    break
                level_of[idx] += 1
            result[idx] = pts if pts is not None else _remove_collinear(loop)
        bad = _find_crossings(result)
        todo = [i for i in sorted(bad) if level_of[i] < len(levels) - 1]
        if not todo:
            break
        for i in todo:
            level_of[i] += 1
    return [p for p in result if p]


def _find_crossings(polys: Sequence[Polygon | None], cell: float = 6.0) -> set:
    """Indizes der Polygone, die sich selbst oder andere schneiden/berühren."""
    segs = []
    grid = defaultdict(list)
    for pi, poly in enumerate(polys):
        if not poly:
            continue
        n = len(poly)
        for si in range(n):
            x0, y0 = poly[si]
            x1, y1 = poly[(si + 1) % n]
            sid = len(segs)
            segs.append((pi, si, n, x0, y0, x1, y1))
            for gx in range(int(math.floor(min(x0, x1) / cell)), int(math.floor(max(x0, x1) / cell)) + 1):
                for gy in range(int(math.floor(min(y0, y1) / cell)), int(math.floor(max(y0, y1) / cell)) + 1):
                    grid[(gx, gy)].append(sid)

    bad = set()
    for ids in grid.values():
        count = len(ids)
        if count < 2:
            continue
        for a in range(count - 1):
            pa, sa, na, ax0, ay0, ax1, ay1 = segs[ids[a]]
            for b in range(a + 1, count):
                pb, sb, nb, bx0, by0, bx1, by1 = segs[ids[b]]
                if pa == pb:
                    if pa in bad:
                        continue
                    diff = abs(sa - sb)
                    if diff == 1 or diff == na - 1:
                        continue  # Nachbarkanten teilen einen Eckpunkt
                elif pa in bad and pb in bad:
                    continue
                if (max(ax0, ax1) < min(bx0, bx1) or max(bx0, bx1) < min(ax0, ax1)
                        or max(ay0, ay1) < min(by0, by1) or max(by0, by1) < min(ay0, ay1)):
                    continue
                if _segments_touch(ax0, ay0, ax1, ay1, bx0, by0, bx1, by1):
                    bad.add(pa)
                    bad.add(pb)
    return bad


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return 0.0 if abs(v) < 1e-9 else v


def _segments_touch(ax0, ay0, ax1, ay1, bx0, by0, bx1, by1) -> bool:
    """True, wenn sich zwei Strecken schneiden oder berühren."""
    d1 = _orient(bx0, by0, bx1, by1, ax0, ay0)
    d2 = _orient(bx0, by0, bx1, by1, ax1, ay1)
    d3 = _orient(ax0, ay0, ax1, ay1, bx0, by0)
    d4 = _orient(ax0, ay0, ax1, ay1, bx1, by1)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return True

    def on_seg(px, py, qx, qy, rx, ry):
        return min(px, qx) - 1e-9 <= rx <= max(px, qx) + 1e-9 and min(py, qy) - 1e-9 <= ry <= max(py, qy) + 1e-9

    return ((d1 == 0 and on_seg(bx0, by0, bx1, by1, ax0, ay0))
            or (d2 == 0 and on_seg(bx0, by0, bx1, by1, ax1, ay1))
            or (d3 == 0 and on_seg(ax0, ay0, ax1, ay1, bx0, by0))
            or (d4 == 0 and on_seg(ax0, ay0, ax1, ay1, bx1, by1)))


# ---------------------------------------------------------------------------
# Verschachtelung
# ---------------------------------------------------------------------------

def _point_in_polygon(x: float, y: float, poly: Sequence[Point]) -> bool:
    inside = False
    n = len(poly)
    x0, y0 = poly[-1]
    for i in range(n):
        x1, y1 = poly[i]
        if (y1 > y) != (y0 > y):
            if x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
        x0, y0 = x1, y1
    return inside


def polygon_depths(polygons: Sequence[Sequence[Point]]) -> list[int]:
    """Verschachtelungstiefe je Polygon: 0 = Außenkontur, 1 = Loch, 2 = Insel im Loch …

    Setzt voraus, dass sich die Polygone nicht schneiden (wie bei
    :func:`trace_contours`).
    """
    boxes = []
    for poly in polygons:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    areas = [abs(polygon_area(p)) for p in polygons]
    depths = []
    for i, poly in enumerate(polygons):
        px, py = poly[0]
        bi = boxes[i]
        depth = 0
        for j, other in enumerate(polygons):
            if j == i or areas[j] <= areas[i]:
                continue  # ein umschließendes Polygon ist immer größer
            bj = boxes[j]
            if bj[0] <= bi[0] and bj[1] <= bi[1] and bj[2] >= bi[2] and bj[3] >= bi[3]:
                if _point_in_polygon(px, py, other):
                    depth += 1
        depths.append(depth)
    return depths


def outer_only(polygons: Sequence[Sequence[Point]]) -> list[Polygon]:
    """Nur die äußersten Umrisse (Löcher gefüllt), z. B. für Ausstechformen."""
    polys = [list(p) for p in polygons if len(p) >= 3]
    return [p for p, d in zip(polys, polygon_depths(polys)) if d == 0]
