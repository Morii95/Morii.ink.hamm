"""OpenSCAD-Codegeneratoren für SCAD Studio.

Erzeugt eigenständige .scad-Dateien (alle Daten eingebettet), die mit
OpenSCAD 2021.01 und neueren Versionen (Manifold) zu STL gerendert werden:

* :func:`relief_scad` – Relief bzw. Lithophanie aus einem Höhenraster
* :func:`silhouette_scad` – flache Figur, Schild, Schlüsselanhänger, Stempel,
  Ausstechform oder Schablone aus Konturpolygonen

Alle Maße bleiben als Customizer-Parameter (Fenster → Customizer) oben in der
Datei änderbar. Aufwendige 3D-Boolesche Operationen werden vermieden, weil
sie in OpenSCAD 2021.01 (CGAL) sehr langsam sind: Das Relief ist ein einziges
polyhedron(), die Figuren entstehen aus 2D-Operationen plus linear_extrude().
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from .imaging import polygon_area, polygon_depths

__all__ = [
    "relief_scad",
    "silhouette_scad",
    "scad_string",
    "scad_comment",
    "RELIEF_MODES",
    "SILHOUETTE_MODES",
    "SILHOUETTE_OPTION_NAMES",
]

Point = tuple[float, float]

#: Modi von silhouette_scad() mit deutscher Bezeichnung (Reihenfolge = Dropdown).
SILHOUETTE_MODES = {
    "extrude": "Flache Figur",
    "plate": "Schild mit Relief",
    "keychain": "Schlüsselanhänger",
    "stamp": "Stempel",
    "cookie_cutter": "Ausstechform",
    "stencil": "Schablone",
}

RELIEF_MODES = ("relief", "lithophane")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _clean_text(text: Any, max_len: int) -> str:
    """Einzeiliger, druckbarer Text ohne Zeichen, die OpenSCAD-Code beenden könnten."""
    text = "" if text is None else str(text)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[:max_len - 1].rstrip() + "…"
    return text


def scad_comment(text: Any, max_len: int = 120) -> str:
    """Macht Text (z. B. einen Titel vom Nutzer) sicher für OpenSCAD-Kommentare.

    Zeilenumbrüche und Steuerzeichen werden zu Leerzeichen, ``/*`` und ``*/``
    werden entschärft. Das Ergebnis kann gefahrlos hinter ``//`` oder in
    einem Blockkommentar stehen.
    """
    text = _clean_text(text, max_len)
    while "/*" in text or "*/" in text:
        text = text.replace("/*", "/ *").replace("*/", "* /")
    return text


def scad_string(text: Any, max_len: int = 120) -> str:
    """Liefert ein sicheres OpenSCAD-Stringliteral inklusive Anführungszeichen.

    Neben Zeilenumbrüchen und Steuerzeichen werden ``"`` durch ``'`` und
    ``\\`` durch ``/`` ersetzt: So werden keine Escape-Sequenzen benötigt,
    die der Customizer von OpenSCAD 2021.01 falsch auswertet (ein String,
    der auf Backslash endet, bringt dessen Parser durcheinander).
    """
    text = scad_comment(text, max_len).replace("\\", "/").replace('"', "'")
    return f'"{text}"'


def _num(value: float, decimals: int = 3) -> str:
    """Kompakte Zahl: höchstens ``decimals`` Nachkommastellen, ohne überflüssige Nullen."""
    text = f"{float(value):.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _header(title: str, kind: str, extra: Sequence[str] = ()) -> list[str]:
    lines = [
        "// " + "=" * 66,
        f"// {scad_comment(title) or 'Ohne Titel'}",
        f"// {kind} – erzeugt mit SCAD Studio",
        "//",
        "// So geht's:",
        "//  1. Datei in OpenSCAD öffnen (Version 2021.01 oder neuer).",
        "//  2. Menü Fenster → Customizer einblenden und die Werte anpassen.",
        "//  3. F6 (Rendern), danach Datei → Exportieren → Als STL exportieren.",
    ]
    lines += [f"// {line}" if line else "//" for line in extra]
    lines += [
        "//",
        "// Die Bilddaten im Abschnitt [Hidden] bitte nicht von Hand ändern.",
        "// " + "=" * 66,
        "",
    ]
    return lines


class _Param:
    """Ein Customizer-Parameter: Name, Standardwert, Bereich, Beschreibung."""

    def __init__(self, name: str, default: Any, description: str,
                 lo: float | None = None, step: float | None = None,
                 hi: float | None = None, choices: dict[str, str] | None = None):
        self.name = name
        self.default = default
        self.description = description
        self.lo, self.step, self.hi = lo, step, hi
        self.choices = choices

    def coerce(self, value: Any) -> Any:
        """Wandelt einen übergebenen Wert passend um; ungültige Werte -> Standardwert."""
        if value is None:
            return self.default
        if isinstance(self.default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "ja", "yes", "on", "wahr")
            return bool(value)
        if self.choices is not None:
            return value if value in self.choices else self.default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return self.default
        if not math.isfinite(number):
            return self.default
        if self.lo is not None and number < self.lo:
            number = self.lo
        return number

    def render(self, value: Any) -> list[str]:
        if isinstance(self.default, bool):
            literal, annotation = ("true" if value else "false"), ""
        elif self.choices is not None:
            literal = f'"{value}"'
            annotation = " // [" + ", ".join(f"{k}:{v}" for k, v in self.choices.items()) + "]"
        else:
            literal = _num(value)
            hi = self.hi if self.hi is None or value <= self.hi else value
            annotation = f" // [{_num(self.lo)}:{_num(self.step)}:{_num(hi)}]" if self.lo is not None else ""
        return [f"// {self.description}", f"{self.name} = {literal};{annotation}"]


def _render_params(groups: Sequence[tuple[str, Sequence[_Param]]], values: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for group, params in groups:
        lines.append(f"/* [{group}] */")
        for p in params:
            lines += p.render(values[p.name])
        lines.append("")
    return lines


def _wrap_items(items: Sequence[str], per_line: int, indent: str = "    ") -> list[str]:
    """Verteilt Listeneinträge auf Zeilen (Komma-getrennt)."""
    lines = []
    for i in range(0, len(items), per_line):
        chunk = ",".join(items[i:i + per_line])
        last = i + per_line >= len(items)
        lines.append(indent + chunk + ("" if last else ","))
    return lines


# ---------------------------------------------------------------------------
# Relief / Lithophanie
# ---------------------------------------------------------------------------

_RELIEF_MODULES = r"""
// Hauptteil
relief();

// Das Relief ist EIN geschlossener Körper (polyhedron). Der Rahmen ist Teil
// des Höhenfelds, es sind keine 3D-Booleschen Operationen nötig – so bleibt
// die Berechnung auch in OpenSCAD 2021 schnell.
module relief() {
    b = max(base, 0.1);       // Mindeststärke, damit der Körper geschlossen bleibt
    d = max(depth, 0);
    f = max(frame, 0);
    k = f > 0 ? 2 : 0;        // Rahmen: zwei zusätzliche Punktreihen je Seite
    step = width / (cols - 1);
    img_h = step * (rows - 1);
    gap = min(f / 2, step / 4); // schmaler Übergang = fast senkrechte Rahmen-Innenkante
    nx = cols + 2 * k;
    ny = rows + 2 * k;
    top = b + d;
    xs = [for (j = [0:nx - 1]) grid_pos(j, nx, k, f, gap, step, width)];
    ys = [for (i = [0:ny - 1]) img_h - grid_pos(i, ny, k, f, gap, step, img_h)];
    // Rand des Gitters, von oben gesehen gegen den Uhrzeigersinn
    edge = concat(
        [for (j = [0:nx - 2]) [ny - 1, j]],
        [for (i = [ny - 1:-1:1]) [i, nx - 1]],
        [for (j = [nx - 1:-1:1]) [0, j]],
        [for (i = [0:ny - 2]) [i, 0]]
    );
    ne = len(edge);
    n_top = nx * ny;
    points = concat(
        // Oberfläche (Rahmenpunkte auf voller Höhe)
        [for (i = [0:ny - 1]) for (j = [0:nx - 1])
            [xs[j], ys[i],
             (i < k || j < k || i >= ny - k || j >= nx - k)
                ? top : b + d * data[i - k][j - k] / 1000]],
        // Bodenrand auf z = 0
        [for (p = edge) [xs[p[1]], ys[p[0]], 0]]
    );
    faces = concat(
        // je Rasterfeld zwei Dreiecke, von außen gesehen im Uhrzeigersinn
        [for (i = [0:ny - 2]) for (j = [0:nx - 2]) let(a = i * nx + j)
            each [[a, a + 1, a + nx + 1], [a, a + nx + 1, a + nx]]],
        // Seitenwände
        [for (m = [0:ne - 1]) let(p = edge[m], q = edge[(m + 1) % ne])
            [p[0] * nx + p[1], q[0] * nx + q[1], n_top + (m + 1) % ne, n_top + m]],
        // Boden als ein Vieleck
        [[for (m = [0:ne - 1]) n_top + m]]
    );
    echo(str(model_title, ": ", width + 2 * f, " x ", img_h + 2 * f, " x ", top, " mm"));
    translate([-width / 2, -img_h / 2, 0])
        polyhedron(points = points, faces = faces, convexity = 10);
}

// Position einer Gitterlinie; bei Rahmen kommen je Seite zwei Linien hinzu
function grid_pos(e, n, k, f, gap, step, size) =
    k == 0 ? e * step :
    e == 0 ? -f :
    e == 1 ? -gap :
    e == n - 2 ? size + gap :
    e == n - 1 ? size + f :
    (e - 2) * step;
"""


def relief_scad(
    grid: Sequence[Sequence[float]],
    *,
    width: float = 100.0,
    depth: float = 3.0,
    base: float = 1.0,
    mode: str = "relief",
    frame: float = 0.0,
    title: str = "Relief",
) -> str:
    """Eigenständige .scad-Datei für ein Höhenfeld (Relief oder Lithophanie).

    ``grid``: Werte 0..1, Zeile 0 = obere Bildkante (wie aus
    ``imaging.heightmap``). Höhe je Punkt: ``z = base + depth * v``; der Boden
    liegt bei z = 0, das Bild ist von oben (+Z) gesehen richtig herum, X =
    Bildbreite, der Körper ist in XY zentriert.

    * ``width``: Breite des Bildbereichs in mm (ohne Rahmen); die Tiefe in Y
      ergibt sich aus dem Seitenverhältnis des Rasters.
    * ``mode``: ``'relief'`` oder ``'lithophane'`` – gleiche Formel, nur
      Beschriftung und Hinweise unterscheiden sich (für Lithophanien übergibt
      die Oberfläche ein invertiertes Raster, dunkel = dick).
    * ``frame``: Rahmenbreite in mm (0 = ohne). Der Rahmen liegt außen um den
      Bildbereich (Gesamtbreite = width + 2·frame) und hat die volle Höhe
      base + depth. Er ist Teil desselben Polyeders: je Seite zwei weitere
      Gitterlinien (Außenkante und – um min(frame/2, Rasterabstand/4) nach
      außen versetzt – Innenkante), wodurch eine fast senkrechte Innenkante
      entsteht.

    Die Datei enthält EIN geschlossenes, mannigfaltiges polyhedron(), dessen
    Punkte und Flächen per List Comprehension aus der eingebetteten
    Datenmatrix (Promille-Werte) berechnet werden. width, depth, base und
    frame bleiben dadurch Customizer-Parameter.
    """
    if mode not in RELIEF_MODES:
        raise ValueError(f"Unbekannter Modus „{mode}“ (erlaubt: relief, lithophane).")
    rows = [list(r) for r in grid]
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ValueError("Das Höhenraster braucht mindestens 2×2 Werte.")
    cols = len(rows[0])
    if any(len(r) != cols for r in rows):
        raise ValueError("Das Höhenraster muss rechteckig sein (alle Zeilen gleich lang).")

    def permille(v: Any) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            x = 0.0
        if not math.isfinite(x):
            x = 0.0
        return str(int(round(min(1.0, max(0.0, x)) * 1000)))

    litho = mode == "lithophane"
    params = [
        ("Abmessungen", [
            _Param("width", 100.0, "Breite des Bildbereichs in mm (X-Richtung, ohne Rahmen)", 10, 1, 400),
            _Param("depth", 3.0,
                   "Dickenunterschied zwischen hellster und dunkelster Stelle in mm" if litho
                   else "Reliefhöhe: Unterschied zwischen tiefster und höchster Stelle in mm",
                   0.2, 0.1, 20),
            _Param("base", 1.0,
                   "Mindeststärke an den hellsten Stellen in mm" if litho
                   else "Stärke der Grundplatte unter dem Relief in mm",
                   0.2, 0.1, 20),
        ]),
        ("Rahmen", [
            _Param("frame", 0.0, "Rahmenbreite in mm (0 = kein Rahmen), außen um das Bild, volle Höhe",
                   0, 0.5, 30),
        ]),
    ]
    by_name = {p.name: p for _, group in params for p in group}
    given = {"width": width, "depth": depth, "base": base, "frame": frame}
    values = {name: by_name[name].coerce(value) for name, value in given.items()}

    if litho:
        kind = "Lithophanie (Durchlichtbild)"
        hints = ["", "Druckhinweise: 100 % Füllung, hochkant drucken, helles Filament;",
                 "das Bild erscheint erst mit Licht von hinten."]
    else:
        kind = "Relief (Höhenbild)"
        hints = []

    lines = _header(title, kind, hints)
    lines += _render_params(params, values)
    lines += [
        "/* [Hidden] */",
        f"model_title = {scad_string(title)};",
        f"// Höhenwerte in Promille (0 = tief, 1000 = hoch), {cols} × {len(rows)} Punkte,",
        "// erste Zeile = obere Bildkante.",
        "function relief_data() = [",
    ]
    last = len(rows) - 1
    for i, row in enumerate(rows):
        lines.append("[" + ",".join(permille(v) for v in row) + "]" + ("" if i == last else ","))
    lines += [
        "];",
        "data = relief_data();",
        "rows = len(data);",
        "cols = len(data[0]);",
    ]
    return "\n".join(lines) + "\n" + _RELIEF_MODULES


# ---------------------------------------------------------------------------
# Figuren aus Konturen
# ---------------------------------------------------------------------------

def _silhouette_params() -> list[tuple[str, list[_Param]]]:
    return [
        ("Allgemein", [
            _Param("mode", "extrude", "Was soll entstehen?", choices=SILHOUETTE_MODES),
            _Param("size", 80.0, "Größe: längere Bildseite in mm", 5, 1, 300),
            _Param("height", 3.0, "Höhe der Figur in mm (Schild/Stempel: wie weit sie heraussteht)", 0.4, 0.2, 30),
            _Param("thicken", 0.0, "Linien verdicken (+) oder verdünnen (-) in mm, hilft bei feinen Strichen",
                   -3, 0.05, 3),
            _Param("corner_radius", 3.0, "Eckenradius von Schild, Stempelblock und Schablone in mm", 0, 0.5, 20),
        ]),
        ("Schild", [
            _Param("plate_margin", 5.0, "Rand um die Figur in mm", 0, 0.5, 50),
            _Param("plate_thickness", 2.0, "Stärke der Grundplatte in mm", 0.6, 0.2, 10),
            _Param("engrave", False, "Figur eingravieren statt erhaben (Tiefe = Höhe, max. Plattenstärke - 0.4)"),
        ]),
        ("Schlüsselanhänger", [
            _Param("backing", 2.0, "Hintergrund-Rand um die Figur in mm, verbindet lose Teile (0 = ohne)",
                   0, 0.5, 10),
            _Param("backing_solid", True, "Hintergrund ohne Löcher (Innenflächen der Figur füllen)"),
            _Param("raise_height", 1.0, "Figur steht so viele mm über dem Hintergrund (0 = alles gleich hoch)",
                   0, 0.2, 5),
            _Param("ring_outer", 10.0, "Außendurchmesser der Öse in mm", 4, 0.5, 30),
            _Param("ring_inner", 5.0, "Lochdurchmesser der Öse in mm", 1, 0.5, 25),
        ]),
        ("Stempel", [
            _Param("stamp_margin", 3.0, "Rand des Stempelblocks um die Figur in mm", 0, 0.5, 30),
            _Param("stamp_thickness", 6.0, "Stärke des Stempelblocks in mm", 1, 0.5, 30),
            _Param("handle", False, "Griff als eigenes Teil daneben erzeugen (auf die Rückseite kleben)"),
            _Param("handle_height", 25.0, "Griffhöhe in mm", 10, 1, 80),
            _Param("handle_diameter", 20.0, "Griffdurchmesser in mm", 8, 1, 60),
        ]),
        ("Ausstechform", [
            _Param("cutter_wall", 0.8, "Wandstärke der Schneide in mm", 0.4, 0.1, 3),
            _Param("cutter_height", 15.0, "Höhe der Ausstechform in mm", 4, 1, 40),
            _Param("flange_width", 4.0, "Breite der Griffkante unten in mm (0 = ohne)", 0, 0.5, 15),
            _Param("flange_height", 1.6, "Höhe der Griffkante in mm", 0.4, 0.2, 5),
        ]),
        ("Schablone", [
            _Param("stencil_margin", 8.0, "Rand um die Figur in mm", 1, 0.5, 50),
            _Param("stencil_thickness", 1.2, "Stärke der Schablone in mm", 0.4, 0.1, 5),
        ]),
    ]


#: Namen der Parameter, die über ``mode_options`` gesetzt werden können.
SILHOUETTE_OPTION_NAMES = tuple(
    p.name for _, group in _silhouette_params() for p in group
    if p.name not in ("mode", "size", "height", "thicken")
)

_SILHOUETTE_MODULES = r"""
// Hauptteil
echo(str(model_title, ": Figur ", fig_w, " x ", fig_h, " mm, Modus ", mode));
if (mode == "plate") mode_plate();
else if (mode == "keychain") mode_keychain();
else if (mode == "stamp") mode_stamp();
else if (mode == "cookie_cutter") mode_cookie_cutter();
else if (mode == "stencil") mode_stencil();
else mode_extrude();

// ---- 2D-Grundformen (Maße in mm, Figur zentriert) ----

// Die Figur, wie sie im Bild steht (Even-Odd: Löcher bleiben frei)
module shape() {
    scale([mm_per_px, mm_per_px]) polygon(points, paths);
}

// Figur mit Verdickung/Verdünnung
module figure() {
    if (thicken != 0) offset(r = thicken) shape();
    else shape();
}

// Nur die Außenumrisse (Löcher gefüllt)
module outline() {
    if (thicken != 0) offset(r = thicken) outline_raw();
    else outline_raw();
}
module outline_raw() {
    scale([mm_per_px, mm_per_px]) polygon(points, outer_paths);
}

module rounded_rect(w, h, r) {
    rr = max(0, min(r, w / 2 - 0.01, h / 2 - 0.01));
    if (rr > 0) offset(r = rr) square([w - 2 * rr, h - 2 * rr], center = true);
    else square([w, h], center = true);
}

// ---- Modi ----

// Flache Figur: einmal extrudiert
module mode_extrude() {
    linear_extrude(height = height) figure();
}

// Schild: Figur erhaben auf (oder eingraviert in) einer Platte mit runden Ecken
module mode_plate() {
    t = plate_thickness;
    if (engrave) {
        cut = max(0.2, min(height, t - 0.4));
        linear_extrude(height = t - cut) plate_shape();
        translate([0, 0, t - cut - eps])
            linear_extrude(height = cut + eps) difference() { plate_shape(); figure(); }
    } else {
        linear_extrude(height = t) plate_shape();
        translate([0, 0, t - eps]) linear_extrude(height = height + eps) figure();
    }
}
module plate_shape() {
    rounded_rect(fig_w + 2 * plate_margin, fig_h + 2 * plate_margin, corner_radius);
}

// Schlüsselanhänger: Hintergrund + Figur + Öse (in 2D verbunden)
module mode_keychain() {
    linear_extrude(height = height) keychain_shape();
    if (raise_height > 0 && backing > 0)
        translate([0, 0, height - eps]) linear_extrude(height = raise_height + eps) figure();
}
module keychain_shape() {
    ri = min(ring_inner, ring_outer - 1.6);
    wall = (ring_outer - ri) / 2;
    // Öse sitzt über dem höchsten Punkt der Figur und überlappt sie
    cx = top_point[0] * mm_per_px;
    cy = top_point[1] * mm_per_px + thicken + backing + ri / 2 + wall / 4;
    difference() {
        union() {
            if (backing > 0) {
                if (backing_solid) offset(r = backing) outline();
                else offset(r = backing) figure();
            } else {
                figure();
            }
            translate([cx, cy]) circle(d = ring_outer);
        }
        translate([cx, cy]) circle(d = ri);
    }
}

// Stempel: gespiegelte Figur auf einem Block (Abdruck ist dann lesbar)
module mode_stamp() {
    t = stamp_thickness;
    linear_extrude(height = t)
        rounded_rect(fig_w + 2 * stamp_margin, fig_h + 2 * stamp_margin, corner_radius);
    translate([0, 0, t - eps])
        linear_extrude(height = height + eps) mirror([1, 0]) figure();
    if (handle)
        translate([fig_w / 2 + stamp_margin + handle_diameter / 2 + 5, 0, 0]) stamp_handle();
}
// Griff: Standfuß (wird auf die Stempelrückseite geklebt), Hals und Knauf.
// Druckbar ohne Stützen (alle Überhänge 45°).
module stamp_handle() {
    d = handle_diameter;
    h = max(handle_height, 0.9 * d + 4);
    rotate_extrude() polygon([
        [0, 0], [d / 2, 0], [d / 2, 2], [0.3 * d, 2 + 0.2 * d],
        [0.3 * d, h - 0.45 * d], [d / 2, h - 0.25 * d], [d / 2, h - 2],
        [d / 2 - 2, h], [0, h]
    ]);
}

// Ausstechform: dünne Wand entlang des Außenumrisses, Griffkante unten.
// Mit der Griffkante nach unten drucken; die Schneide zeigt nach oben.
// Die Wand steht nur minimal überlappend AUF der Griffkante – das halbiert
// die Rechenzeit der 3D-Vereinigung in OpenSCAD 2021 gegenüber zwei
// ineinander steckenden Körpern.
module mode_cookie_cutter() {
    fh = flange_width > 0 ? min(flange_height, cutter_height - 1) : 0;
    if (fh > 0)
        linear_extrude(height = fh)
            difference() { offset(r = cutter_wall + flange_width) outline(); outline(); }
    translate([0, 0, fh > 0 ? fh - eps : 0])
        linear_extrude(height = fh > 0 ? cutter_height - fh + eps : cutter_height)
            difference() { offset(r = cutter_wall) outline(); outline(); }
}

// Schablone: Platte, aus der die Figur ausgeschnitten ist.
// Hinweis: Innenflächen (z. B. das Innere eines "O") fallen heraus.
module mode_stencil() {
    linear_extrude(height = stencil_thickness) difference() {
        rounded_rect(fig_w + 2 * stencil_margin, fig_h + 2 * stencil_margin, corner_radius);
        figure();
    }
}
"""


def _top_point(polys: Sequence[Sequence[Point]]) -> Point:
    """Höchster Punkt der Figur; bei mehreren (fast) gleich hohen der mittigste."""
    ymax = max(y for poly in polys for _, y in poly)
    candidates = [(x, y) for poly in polys for x, y in poly if y >= ymax - 0.5]
    return min(candidates, key=lambda p: abs(p[0]))


def silhouette_scad(
    polygons: Sequence[Sequence[Point]],
    img_w: int,
    img_h: int,
    *,
    mode: str = "extrude",
    size: float = 80.0,
    height: float = 3.0,
    thicken: float = 0.0,
    title: str = "Form",
    **mode_options: Any,
) -> str:
    """Eigenständige .scad-Datei für eine Figur aus Konturpolygonen.

    ``polygons`` sind geschlossene Polygone in Pixelkoordinaten mit Y nach
    oben (wie aus ``imaging.trace_contours``), Außenkonturen und Löcher
    gemischt; sie werden als EIN ``polygon(points, paths)`` mit Even-Odd-
    Füllung ausgegeben. Maßstab: die längere Bildseite (``img_w``/``img_h``
    in Pixeln) entspricht ``size`` mm. Die Figur (ihr Begrenzungsrechteck)
    ist im Ursprung zentriert, die Unterseite liegt bei z = 0.

    Alle Modi stecken in derselben Datei und sind über das Customizer-Dropdown
    ``mode`` umschaltbar: extrude (Flache Figur), plate (Schild mit Relief),
    keychain (Schlüsselanhänger), stamp (Stempel), cookie_cutter
    (Ausstechform), stencil (Schablone).

    Gemeinsame Parameter: ``size`` (mm), ``height`` (mm: Höhe der Figur bzw.
    wie weit sie bei Schild/Stempel heraussteht; beim Anhänger Stärke des
    Körpers), ``thicken`` (mm, offset(r) auf die Figur; negativ = dünner).

    ``mode_options`` überschreibt Standardwerte (unbekannte Namen werden
    ignoriert, Werte unter dem Minimum angehoben):

    * alle:           ``corner_radius`` = 3 (Ecken von Schild/Stempel/Schablone)
    * plate:          ``plate_margin`` = 5, ``plate_thickness`` = 2,
                      ``engrave`` = False (Tiefe = height, höchstens
                      plate_thickness - 0.4)
    * keychain:       ``backing`` = 2 (offset-Rand, 0 = ohne),
                      ``backing_solid`` = True (Innenflächen im Hintergrund
                      füllen), ``raise_height`` = 1 (Figur steht so weit über dem
                      Hintergrund, 0 = eine Höhe), ``ring_outer`` = 10,
                      ``ring_inner`` = 5 (Durchmesser der Öse)
    * stamp:          ``stamp_margin`` = 3, ``stamp_thickness`` = 6,
                      ``handle`` = False (Griff als eigenes Teil zum
                      Aufkleben), ``handle_height`` = 25, ``handle_diameter`` = 20
    * cookie_cutter:  ``cutter_wall`` = 0.8, ``cutter_height`` = 15,
                      ``flange_width`` = 4, ``flange_height`` = 1.6
    * stencil:        ``stencil_margin`` = 8, ``stencil_thickness`` = 1.2

    Rechenzeit: extrude, keychain (mit raise_height = 0) und stencil bestehen nur aus
    2D-Operationen und einem linear_extrude(); plate, stamp, cookie_cutter und
    keychain mit raise_height > 0 brauchen genau eine 3D-Vereinigung.
    """
    if mode not in SILHOUETTE_MODES:
        raise ValueError(
            f"Unbekannter Modus „{mode}“ (erlaubt: {', '.join(SILHOUETTE_MODES)})."
        )
    if img_w <= 0 or img_h <= 0:
        raise ValueError("Bildbreite und -höhe müssen größer als 0 sein.")

    polys: list[list[Point]] = []
    for poly in polygons:
        pts = [(float(x), float(y)) for x, y in poly]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        if len(pts) >= 3 and abs(polygon_area(pts)) > 0:
            polys.append(pts)
    if not polys:
        raise ValueError(
            "Im Bild wurde keine Form gefunden. Tipp: Schwellwert ändern oder „Invertieren“ ausprobieren."
        )

    xs = [x for poly in polys for x, _ in poly]
    ys = [y for poly in polys for _, y in poly]
    # Mitte auf 0,01 px runden: die verschobenen Koordinaten bleiben exakt
    # zweistellig, das Runden bei der Ausgabe verändert die Kontur nicht.
    cx, cy = round((min(xs) + max(xs)) / 2, 2), round((min(ys) + max(ys)) / 2, 2)
    shape_w, shape_h = max(xs) - min(xs), max(ys) - min(ys)
    centered = [[(x - cx, y - cy) for x, y in poly] for poly in polys]
    depths = polygon_depths(centered)
    outer_idx = [i for i, d in enumerate(depths) if d == 0]
    top = _top_point([centered[i] for i in outer_idx])

    groups = _silhouette_params()
    by_name = {p.name: p for _, group in groups for p in group}
    values = {name: p.default for name, p in by_name.items()}
    for name, value in mode_options.items():
        if name in SILHOUETTE_OPTION_NAMES:
            values[name] = by_name[name].coerce(value)
    values["mode"] = mode
    values["size"] = by_name["size"].coerce(size)
    values["height"] = by_name["height"].coerce(height)
    values["thicken"] = by_name["thicken"].coerce(thicken)

    # Punkte und Pfade: Pfade als Indexbereiche, das spart die halbe Dateigröße
    point_items: list[str] = []
    ranges: list[tuple[int, int]] = []
    for poly in centered:
        start = len(point_items)
        point_items += [f"[{_num(x, 2)},{_num(y, 2)}]" for x, y in poly]
        ranges.append((start, len(point_items) - 1))
    range_items = [f"[{a},{b}]" for a, b in ranges]
    outer_items = [range_items[i] for i in outer_idx]

    lines = _header(title, "Figur aus Bildkonturen", [
        "",
        "Im Customizer unter „Allgemein → mode“ wählst du, was entsteht:",
        "Flache Figur, Schild, Schlüsselanhänger, Stempel, Ausstechform oder",
        "Schablone. Die Einstellungen der übrigen Modi werden dann ignoriert.",
    ])
    lines += _render_params(groups, values)
    lines += [
        "/* [Hidden] */",
        f"model_title = {scad_string(title)};",
        "$fa = 6;",
        "$fs = 0.4;",
        "eps = 0.01;",
        "// Bildgröße in Pixeln (die längere Seite entspricht size)",
        f"img_w = {_num(img_w)};",
        f"img_h = {_num(img_h)};",
        "// Größe der Figur in Pixeln und ihr höchster Punkt (für die Öse)",
        f"shape_size = [{_num(shape_w, 2)}, {_num(shape_h, 2)}];",
        f"top_point = [{_num(top[0], 2)}, {_num(top[1], 2)}];",
        "mm_per_px = size / max(img_w, img_h);",
        "fig_w = shape_size[0] * mm_per_px + 2 * thicken;",
        "fig_h = shape_size[1] * mm_per_px + 2 * thicken;",
        "",
        f"// Konturpunkte in Pixeln, Y nach oben, um die Mitte der Figur ({len(point_items)} Punkte)",
        "function shape_points() = [",
    ]
    lines += _wrap_items(point_items, 8)
    lines += [
        "];",
        f"// Konturen als Indexbereiche [erster, letzter] in shape_points() ({len(ranges)} Konturen)",
        "function shape_ranges() = [",
    ]
    lines += _wrap_items(range_items, 10)
    lines += [
        "];",
        "// Nur die Außenumrisse (für Ausstechform und gefüllten Hintergrund)",
        "function outer_ranges() = [",
    ]
    lines += _wrap_items(outer_items, 10)
    lines += [
        "];",
        "points = shape_points();",
        "paths = [for (r = shape_ranges()) [for (i = [r[0]:r[1]]) i]];",
        "outer_paths = [for (r = outer_ranges()) [for (i = [r[0]:r[1]]) i]];",
    ]
    return "\n".join(lines) + "\n" + _SILHOUETTE_MODULES
