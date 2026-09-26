"""Qualitätsprüfung für STL-Dateien (nur Python-Standardbibliothek).

Beantwortet für ein gerendertes Modell die Fragen:

* Stimmen die Maße?            -> Bounding-Box, Größe, Volumen, Oberfläche
* Ist die STL fehlerfrei?      -> offene Kanten, nicht-mannigfaltige Kanten,
                                  entartete Dreiecke, getrennte Teile
* Sind alle Wände vorhanden
  und richtig orientiert?      -> Normalen/Umlaufsinn, innen-außen vertauscht,
                                  Hohlräume
* Ist das Teil druckbar?       -> Druckbett (inkl. Drehen/Kippen), Wandstärke
                                  (Schätzung per Strahlverfolgung), Auflagefläche,
                                  Überhänge

Öffentliche API::

    mesh = read_stl("teil.stl")          # binär oder ASCII
    result = analyze("teil.stl")         # JSON-fähiges dict
    print(summary_text(result))
    stl_bbox("teil.stl")                 # schnelle Bounding-Box ohne Analyse

Alle Längen in mm. Die Prüfung ist bewusst ohne numpy/trimesh geschrieben und
bleibt durch O(n)-Topologie und ein dünn besetztes Gitter auch bei ~300 000
Dreiecken in wenigen Sekunden fertig.
"""

from __future__ import annotations

import bisect
import itertools
import math
import os
import random
import re
import struct
import time
from array import array
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

__all__ = ["Mesh", "read_stl", "analyze", "summary_text", "stl_bbox"]

DEFAULT_BED = (220.0, 220.0, 250.0)   # Anycubic Kobra 2 Neo
WELD_QUANTUM = 1e-5                   # Eckpunkte näher als ~1e-5 mm verschmelzen
_BIN_CHUNK = 4096                     # Dreiecke pro struct-Aufruf
_MAX_COORD = 1e7                      # größere Koordinaten = sicher Datenmüll
_NESTING_MAX_SHELLS = 300             # darüber keine Hohlraum-/Verschachtelungsprüfung
_GRID_FACTOR = 2.2                    # Gitterzelle ~ Faktor * typische Dreiecksgröße

WALL_NOTE = ("Schätzung: von zufälligen Oberflächenpunkten wird senkrecht ins "
             "Material gemessen – sehr kleine dünne Stellen können übersehen werden.")


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------

class Mesh:
    """Dreiecksnetz mit verschweißten Eckpunkten.

    ``vertices``  – array('d'), flach: x0, y0, z0, x1, y1, z1, ...
    ``triangles`` – array('i'), flach: a0, b0, c0, a1, ... (Indizes in vertices)
    ``file_normals`` – array('f') mit den in der Datei gespeicherten Normalen
    (3 pro Dreieck) oder None.
    """

    __slots__ = ("vertices", "triangles", "file_normals", "source", "fmt", "notes")

    def __init__(self, vertices: Iterable[float], triangles: Iterable[int],
                 file_normals: Optional[Iterable[float]] = None,
                 source: str = "", fmt: str = "") -> None:
        self.vertices = vertices if isinstance(vertices, array) else array("d", vertices)
        self.triangles = triangles if isinstance(triangles, array) else array("i", triangles)
        if file_normals is not None and not isinstance(file_normals, array):
            file_normals = array("f", file_normals)
        self.file_normals = file_normals
        self.source = source
        self.fmt = fmt
        self.notes: List[str] = []

    @property
    def triangle_count(self) -> int:
        return len(self.triangles) // 3

    @property
    def vertex_count(self) -> int:
        return len(self.vertices) // 3

    def bbox(self) -> Tuple[List[float], List[float]]:
        v = self.vertices
        if not v:
            return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        xs, ys, zs = v[0::3], v[1::3], v[2::3]
        return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]

    @classmethod
    def from_triangles(cls, triangles: Iterable[Sequence[Sequence[float]]],
                       quantum: float = WELD_QUANTUM) -> "Mesh":
        """Baut ein Netz aus [(p0, p1, p2), ...] – praktisch für Tests."""
        flat: List[float] = []
        for tri in triangles:
            for p in tri:
                flat.extend((float(p[0]), float(p[1]), float(p[2])))
        verts, tris = _weld([flat], quantum)
        return cls(verts, tris, None, "", "memory")

    def __repr__(self) -> str:
        return f"<Mesh {self.triangle_count} Dreiecke, {self.vertex_count} Eckpunkte>"


# ---------------------------------------------------------------------------
# Einlesen (binär + ASCII)
# ---------------------------------------------------------------------------

_STRUCT_CACHE: Dict[int, Tuple[struct.Struct, struct.Struct]] = {}

_RE_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", re.IGNORECASE)
_RE_NORMAL = re.compile(rb"facet\s+normal\s+(\S+)\s+(\S+)\s+(\S+)", re.IGNORECASE)
_RE_ASCII_HINT = re.compile(rb"\b(facet|endsolid)\b", re.IGNORECASE)


def _bin_structs(n: int) -> Tuple[struct.Struct, struct.Struct]:
    """struct-Objekte für n Datensätze: (9 Eckpunkt-Floats, 3 Normalen-Floats)."""
    s = _STRUCT_CACHE.get(n)
    if s is None:
        if len(_STRUCT_CACHE) > 8:
            _STRUCT_CACHE.clear()
        s = (struct.Struct("<" + "12x9f2x" * n), struct.Struct("<" + "3f38x" * n))
        _STRUCT_CACHE[n] = s
    return s


def _detect(data: bytes) -> Tuple[str, int, str]:
    """Erkennt das Format. Gibt (art, dreiecksanzahl, hinweis) zurück."""
    size = len(data)
    if size == 0:
        raise ValueError("STL-Datei ist leer (0 Bytes).")
    n = -1
    if size >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        # Binär-STL: 80 Byte Kopf + 4 Byte Anzahl + 50 Byte je Dreieck
        if 84 + 50 * n == size:
            return "binary", n, ""
    if data[:1024].lstrip().lower().startswith(b"solid") and _RE_ASCII_HINT.search(data):
        return "ascii", -1, ""
    if n > 0 and size > 84 + 50 * n:
        return "binary", n, (f"Datei hat {size - 84 - 50 * n} überzählige Bytes am Ende "
                             "(ignoriert).")
    if n > 0 and size >= 134:
        raise ValueError(
            f"Keine gültige STL-Datei oder Datei abgeschnitten: Kopf nennt {n} Dreiecke "
            f"({84 + 50 * n} Bytes), die Datei hat aber nur {size} Bytes.")
    raise ValueError("Keine gültige STL-Datei (weder binäres STL noch ASCII-STL erkannt).")


def _check_chunk(vals: Sequence[float]) -> None:
    if not vals:
        return
    if not math.isfinite(sum(vals)):
        raise ValueError("STL-Datei enthält ungültige Koordinaten (NaN/unendlich) – "
                         "Datei beschädigt?")
    if max(vals) > _MAX_COORD or min(vals) < -_MAX_COORD:
        raise ValueError("STL-Datei enthält unplausibel große Koordinaten (> 10 km) – "
                         "Datei beschädigt oder keine STL?")


def _weld(chunks: Iterable[Sequence[float]], quantum: float = WELD_QUANTUM
          ) -> Tuple[array, array]:
    """Verschweißt Eckpunkte. chunks: flache Float-Folgen (9 Werte je Dreieck)."""
    index: Dict[Tuple[float, float, float], int] = {}
    setd = index.setdefault
    ln = index.__len__
    tri = array("i")
    for vals in chunks:
        _check_chunk(vals)
        it = iter(vals)
        # 1. Schritt: exakt gleiche Koordinaten (sehr schnell, C-Ebene)
        tri.extend([setd(p, ln()) for p in zip(it, it, it)])
    pts = list(index)
    del index
    # 2. Schritt: fast gleiche Punkte (Raster ~1e-5 mm) zusammenlegen
    q = 1.0 / quantum
    qindex: Dict[Tuple[int, int, int], int] = {}
    coords: List[Tuple[float, float, float]] = []
    remap: List[int] = []
    for p in pts:
        key = (round(p[0] * q), round(p[1] * q), round(p[2] * q))
        j = qindex.get(key)
        if j is None:
            j = qindex[key] = len(coords)
            coords.append(p)
        remap.append(j)
    if len(coords) != len(pts):
        tri = array("i", [remap[i] for i in tri])
    verts = array("d", itertools.chain.from_iterable(coords))
    return verts, tri


def _binary_chunks(data: bytes, n: int, normals: Optional[array]):
    off = 84
    done = 0
    while done < n:
        k = min(_BIN_CHUNK, n - done)
        vs, ns = _bin_structs(k)
        if normals is not None:
            normals.extend(ns.unpack_from(data, off))
        yield vs.unpack_from(data, off)
        off += 50 * k
        done += k


def _ascii_values(data: bytes) -> Tuple[List[float], Optional[List[float]]]:
    try:
        vals = [float(x) for tup in _RE_VERTEX.findall(data) for x in tup]
    except ValueError:
        raise ValueError("ASCII-STL enthält ungültige Zahlen in einer 'vertex'-Zeile.") from None
    if len(vals) % 9:
        raise ValueError("ASCII-STL ist fehlerhaft: Anzahl der Eckpunkte ist kein "
                         "Vielfaches von 3.")
    normals: Optional[List[float]]
    try:
        normals = [float(x) for tup in _RE_NORMAL.findall(data) for x in tup]
    except ValueError:
        normals = None
    if normals is not None and len(normals) * 3 != len(vals):
        normals = None
    return vals, normals


def read_stl(path: Union[str, os.PathLike]) -> Mesh:
    """Liest eine binäre oder ASCII-STL-Datei und verschweißt die Eckpunkte.

    Löst ``ValueError`` (deutsche Meldung) bei unlesbaren/kaputten Dateien aus,
    ``OSError`` wenn die Datei fehlt.
    """
    data = Path(path).read_bytes()
    kind, n, note = _detect(data)
    if kind == "binary":
        normals = array("f")
        verts, tris = _weld(_binary_chunks(data, n, normals))
        mesh = Mesh(verts, tris, normals, str(path), "binary")
    else:
        vals, nvals = _ascii_values(data)
        del data
        verts, tris = _weld([vals])
        mesh = Mesh(verts, tris, nvals, str(path), "ascii")
    if note:
        mesh.notes.append(note)
    return mesh


def stl_bbox(path: Union[str, os.PathLike]) -> Dict[str, Any]:
    """Schnelle Bounding-Box ohne Topologie/Analyse.

    Rückgabe: {"min": [x,y,z], "max": [...], "size": [...], "triangles": n}
    """
    data = Path(path).read_bytes()
    kind, n, _ = _detect(data)
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    if kind == "binary":
        chunks: Iterable[Sequence[float]] = _binary_chunks(data, n, None)
    else:
        vals, _n = _ascii_values(data)
        n = len(vals) // 9
        chunks = [vals]
    for vals in chunks:
        _check_chunk(vals)
        if not vals:
            continue
        for ax in range(3):
            sl = vals[ax::3]
            lo[ax] = min(lo[ax], min(sl))
            hi[ax] = max(hi[ax], max(sl))
    if n <= 0:
        lo = [0.0] * 3
        hi = [0.0] * 3
    return {
        "min": [round(v, 4) for v in lo],
        "max": [round(v, 4) for v in hi],
        "size": [round(hi[i] - lo[i], 2) for i in range(3)],
        "triangles": n,
    }


# ---------------------------------------------------------------------------
# Geometrie je Dreieck
# ---------------------------------------------------------------------------

def _geometry(X, Y, Z, A, B, C, ref):
    """Fläche, Einheitsnormale und 6-faches Tetraedervolumen je Dreieck."""
    T = len(A)
    area = [0.0] * T
    NX = [0.0] * T
    NY = [0.0] * T
    NZ = [0.0] * T
    vol6 = [0.0] * T
    rx, ry, rz = ref
    sqrt = math.sqrt
    degenerate = 0
    for f, (a, b, c) in enumerate(zip(A, B, C)):
        ax = X[a]
        ay = Y[a]
        az = Z[a]
        ux = X[b] - ax
        uy = Y[b] - ay
        uz = Z[b] - az
        vx = X[c] - ax
        vy = Y[c] - ay
        vz = Z[c] - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        vol6[f] = (ax - rx) * nx + (ay - ry) * ny + (az - rz) * nz
        l2 = nx * nx + ny * ny + nz * nz
        # entartet: Fläche ~0 oder Winkel < ~0.0006° (kollinear)
        if l2 < 4e-20 or l2 <= 1e-10 * (ux * ux + uy * uy + uz * uz) * (vx * vx + vy * vy + vz * vz):
            degenerate += 1
            continue
        ln = sqrt(l2)
        area[f] = 0.5 * ln
        NX[f] = nx / ln
        NY[f] = ny / ln
        NZ[f] = nz / ln
    return area, NX, NY, NZ, vol6, degenerate


# ---------------------------------------------------------------------------
# Topologie: Kanten, Orientierung, zusammenhängende Teile
# ---------------------------------------------------------------------------

def _topology(A, B, C, N):
    """Kantenanalyse in O(n).

    Halbkante s = 3*f + i (i = 0: a->b, 1: b->c, 2: c->a). Jede ungerichtete
    Kante sollte genau zwei Halbkanten in entgegengesetzter Richtung haben.
    """
    T = len(A)
    M = 3 * T
    keys: List[int] = [0] * M
    dirs: List[bool] = [False] * M
    keys[0::3] = [a * N + b if a < b else b * N + a for a, b in zip(A, B)]
    keys[1::3] = [a * N + b if a < b else b * N + a for a, b in zip(B, C)]
    keys[2::3] = [a * N + b if a < b else b * N + a for a, b in zip(C, A)]
    dirs[0::3] = [a < b for a, b in zip(A, B)]
    dirs[1::3] = [a < b for a, b in zip(B, C)]
    dirs[2::3] = [a < b for a, b in zip(C, A)]

    pos: Dict[int, int] = {}
    setd = pos.setdefault
    mate = [-1] * M                # Partner-Halbkante (nur bei genau 2 Flächen)
    multi: Dict[int, List[int]] = {}   # Kanten mit > 2 Flächen
    mis = 0                        # Kanten, die zweimal gleich herum laufen
    for s, k in enumerate(keys):
        s0 = setd(k, s)
        if s0 == s:
            continue
        m = mate[s0]
        if m < 0 and k not in multi:
            mate[s0] = s
            mate[s] = s0
            if dirs[s0] == dirs[s]:
                mis += 1
        else:
            lst = multi.get(k)
            if lst is None:
                lst = multi[k] = [s0, m]
                mate[s0] = -1
                mate[m] = -1
                if dirs[s0] == dirs[m]:
                    mis -= 1
            lst.append(s)
    del pos

    open_slots = [s for s, m in enumerate(mate) if m < 0]
    if multi:
        open_slots = [s for s in open_slots if keys[s] not in multi]
    del keys

    # Flächen über mannigfaltige Kanten zu "Patches" verbinden und dabei die
    # relative Orientierung weitergeben (par = 1: gegenüber Startfläche gedreht)
    patch = [-1] * T
    par = bytearray(T)
    npatch = 0
    conflicts = 0
    for seed in range(T):
        if patch[seed] >= 0:
            continue
        pid = npatch
        npatch += 1
        patch[seed] = pid
        stack = [seed]
        pop = stack.pop
        push = stack.append
        while stack:
            g = pop()
            pg = par[g]
            s0 = 3 * g
            for s in (s0, s0 + 1, s0 + 2):
                m = mate[s]
                if m < 0:
                    continue
                h = m // 3
                w = pg ^ (dirs[s] == dirs[m])
                if patch[h] < 0:
                    patch[h] = pid
                    par[h] = w
                    push(h)
                elif par[h] != w:
                    conflicts += 1

    patch_open = [False] * npatch
    for s in open_slots:
        patch_open[patch[s // 3]] = True

    # Teile (Shells): Patches, die über nicht-mannigfaltige Kanten hängen, gehören zusammen
    parent = list(range(npatch))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for lst in multi.values():
        r0 = find(patch[lst[0] // 3])
        for s in lst[1:]:
            r = find(patch[s // 3])
            if r != r0:
                parent[r] = r0
    shell_of_patch = [find(p) for p in range(npatch)]
    shells = len(set(shell_of_patch))

    return {
        "open_edges": len(open_slots),
        "nonmanifold_edges": len(multi),
        "misoriented_edges": mis,
        "patch": patch,
        "par": par,
        "npatch": npatch,
        "patch_open": patch_open,
        "shell_of_patch": shell_of_patch,
        "shells": shells,
        "conflicts": conflicts // 2,
    }


# ---------------------------------------------------------------------------
# Dünn besetztes Gitter für Strahltests
# ---------------------------------------------------------------------------

class _Grid:
    """Gleichmäßiges, dünn besetztes Voxelgitter (dict Zelle -> Dreiecksliste)."""

    def __init__(self, X, Y, Z, A, B, C, area, NX, NY, NZ, bmin, bmax):
        good = [a for a in area if a > 0.0]
        total = sum(good)
        ext = max(bmax[i] - bmin[i] for i in range(3))
        if not good or ext <= 0:
            s = max(ext, 1.0)
        else:
            good.sort()
            median = good[len(good) // 2]
            # Zellgröße ~ typische Dreiecksgröße, aber Gesamtzahl der Einträge begrenzen
            s = _GRID_FACTOR * math.sqrt(median)
            s = max(s, math.sqrt(1.6 * total / (3 * len(good) + 200000)))
        s = max(s, ext / 1024.0, 1e-6)
        self.s = s
        self.inv = inv = 1.0 / s
        org = [bmin[i] - 0.25 * s for i in range(3)]
        dims = [int((bmax[i] - org[i]) * inv) + 1 for i in range(3)]
        self.org = org
        self.dims = dims
        nx, ny, nz = dims
        ox, oy, oz = org
        sy = nx
        sz = nx * ny
        strides = (1, sy, sz)
        mx, my, mz = nx - 1, ny - 1, nz - 1
        # Zellindex je Eckpunkt einmal vorab berechnen (floor ist monoton ->
        # Zell-Bounding-Box des Dreiecks = min/max der Eckpunkt-Zellen)
        ix = [min(max(int((x - ox) * inv), 0), mx) for x in X]
        iy = [min(max(int((y - oy) * inv), 0), my) for y in Y]
        iz = [min(max(int((z - oz) * inv), 0), mz) for z in Z]
        vkey = [i + sy * j + sz * k for i, j, k in zip(ix, iy, iz)]
        # Versatz-Tabellen für kleine Zellblöcke (bis 3 Zellen je Achse)
        offs = {}
        for dk in range(3):
            for dj in range(3):
                for di in range(3):
                    offs[di + 3 * dj + 9 * dk] = tuple(
                        i + sy * j + sz * k
                        for k in range(dk + 1) for j in range(dj + 1) for i in range(di + 1))
        cells: Dict[int, List[int]] = defaultdict(list)
        for f, a, b, c in zip(range(len(A)), A, B, C):
            if area[f] <= 0.0:
                continue
            ka = vkey[a]
            if ka == vkey[b] == vkey[c]:
                cells[ka].append(f)
                continue
            xa = ix[a]; xb = ix[b]; xc = ix[c]
            ya = iy[a]; yb = iy[b]; yc = iy[c]
            za = iz[a]; zb = iz[b]; zc = iz[c]
            i0 = min(xa, xb, xc)
            j0 = min(ya, yb, yc)
            k0 = min(za, zb, zc)
            di = max(xa, xb, xc) - i0
            dj = max(ya, yb, yc) - j0
            dk = max(za, zb, zc) - k0
            if di < 3 and dj < 3 and dk < 3 and (di + 1) * (dj + 1) * (dk + 1) <= 12:
                base = i0 + sy * j0 + sz * k0
                for o in offs[di + 3 * dj + 9 * dk]:
                    cells[base + o].append(f)
                continue
            # großes Dreieck: nur Zellen entlang der Ebene belegen
            self._add_big(cells, f, ((X[a], Y[a], Z[a]), (X[b], Y[b], Z[b]), (X[c], Y[c], Z[c])),
                          (NX[f], NY[f], NZ[f]), (i0, j0, k0), (i0 + di, j0 + dj, k0 + dk),
                          strides)
        self.cells = cells

    def _add_big(self, cells, f, P, n, lo, hi, strides):
        an = (abs(n[0]), abs(n[1]), abs(n[2]))
        if an[0] >= an[1] and an[0] >= an[2]:
            w = 0
        elif an[1] >= an[2]:
            w = 1
        else:
            w = 2
        u = (w + 1) % 3
        v = (w + 2) % 3
        s = self.s
        inv = self.inv
        org = self.org
        nw = n[w]
        cu = -n[u] / nw
        cv = -n[v] / nw
        c0 = (n[0] * P[0][0] + n[1] * P[0][1] + n[2] * P[0][2]) / nw
        wlo = min(P[0][w], P[1][w], P[2][w])
        whi = max(P[0][w], P[1][w], P[2][w])
        ou, ov, ow = org[u], org[v], org[w]
        su, sv, sw = strides[u], strides[v], strides[w]
        kmin, kmax = lo[w], hi[w]
        jmin, jmax = lo[v], hi[v]
        eps = 1e-9 * s
        # Kanten der Projektion in die (u, v)-Ebene, jeweils mit pu <= qu
        edges = []
        for p, q in ((P[0], P[1]), (P[1], P[2]), (P[2], P[0])):
            if p[u] <= q[u]:
                edges.append((p[u], p[v], q[u], q[v]))
            else:
                edges.append((q[u], q[v], p[u], p[v]))
        inf = math.inf
        for iu in range(lo[u], hi[u] + 1):
            U0 = ou + iu * s
            U1 = U0 + s
            # v-Bereich des Dreiecks innerhalb des Streifens U0..U1
            vmin = inf
            vmax = -inf
            for pu, pv, qu, qv in edges:
                if qu < U0 or pu > U1:
                    continue
                if qu - pu <= 1e-12:
                    va, vb = pv, qv
                else:
                    slope = (qv - pv) / (qu - pu)
                    va = pv + ((U0 if U0 > pu else pu) - pu) * slope
                    vb = pv + ((U1 if U1 < qu else qu) - pu) * slope
                if va > vb:
                    va, vb = vb, va
                if va < vmin:
                    vmin = va
                if vb > vmax:
                    vmax = vb
            if vmin > vmax:
                continue
            j0 = int((vmin - ov) * inv - 1e-9)
            j1 = int((vmax - ov) * inv + 1e-9)
            if j0 < jmin:
                j0 = jmin
            if j1 > jmax:
                j1 = jmax
            a1 = cu * U0
            a2 = cu * U1
            if a1 > a2:
                a1, a2 = a2, a1
            for iv in range(j0, j1 + 1):
                V0 = ov + iv * s
                b1 = cv * V0
                b2 = cv * (V0 + s)
                if b1 > b2:
                    b1, b2 = b2, b1
                wl = c0 + a1 + b1
                wh = c0 + a2 + b2
                if wl < wlo:
                    wl = wlo
                if wh > whi:
                    wh = whi
                if wl > wh + eps:
                    continue
                k0 = int((wl - ow) * inv)
                k1 = int((wh - ow) * inv)
                if k0 < kmin:
                    k0 = kmin
                if k1 > kmax:
                    k1 = kmax
                base = iu * su + iv * sv
                for k in range(k0, k1 + 1):
                    cells[base + k * sw].append(f)

    def traverse(self, px, py, pz, dx, dy, dz, tmax=math.inf):
        """3D-DDA: liefert (Dreiecksliste, t_eintritt) für belegte Zellen entlang des Strahls."""
        s = self.s
        inv = self.inv
        ox, oy, oz = self.org
        nx, ny, nz = self.dims
        i = min(max(int((px - ox) * inv), 0), nx - 1)
        j = min(max(int((py - oy) * inv), 0), ny - 1)
        k = min(max(int((pz - oz) * inv), 0), nz - 1)
        inf = math.inf
        if dx > 0:
            si, tx, dtx = 1, (ox + (i + 1) * s - px) / dx, s / dx
        elif dx < 0:
            si, tx, dtx = -1, (ox + i * s - px) / dx, -s / dx
        else:
            si, tx, dtx = 0, inf, inf
        if dy > 0:
            sj, ty, dty = 1, (oy + (j + 1) * s - py) / dy, s / dy
        elif dy < 0:
            sj, ty, dty = -1, (oy + j * s - py) / dy, -s / dy
        else:
            sj, ty, dty = 0, inf, inf
        if dz > 0:
            sk, tz, dtz = 1, (oz + (k + 1) * s - pz) / dz, s / dz
        elif dz < 0:
            sk, tz, dtz = -1, (oz + k * s - pz) / dz, -s / dz
        else:
            sk, tz, dtz = 0, inf, inf
        if si == 0 and sj == 0 and sk == 0:
            return
        key = i + nx * (j + ny * k)
        kx, ky, kz = si, sj * nx, sk * nx * ny
        get = self.cells.get
        t = 0.0
        while True:
            lst = get(key)
            if lst is not None:
                yield lst, t
            if tx <= ty and tx <= tz:
                t = tx
                i += si
                if i < 0 or i >= nx:
                    return
                key += kx
                tx += dtx
            elif ty <= tz:
                t = ty
                j += sj
                if j < 0 or j >= ny:
                    return
                key += ky
                ty += dty
            else:
                t = tz
                k += sk
                if k < 0 or k >= nz:
                    return
                key += kz
                tz += dtz
            if t > tmax:
                return


def _ray_hit(X, Y, Z, a, b, c, px, py, pz, dx, dy, dz, eps):
    """Möller–Trumbore; gibt t (>= 0) oder -1.0 zurück."""
    ax = X[a]; ay = Y[a]; az = Z[a]
    e1x = X[b] - ax; e1y = Y[b] - ay; e1z = Z[b] - az
    e2x = X[c] - ax; e2y = Y[c] - ay; e2z = Z[c] - az
    qx = dy * e2z - dz * e2y
    qy = dz * e2x - dx * e2z
    qz = dx * e2y - dy * e2x
    det = e1x * qx + e1y * qy + e1z * qz
    if -1e-14 < det < 1e-14:
        return -1.0
    idet = 1.0 / det
    tx = px - ax; ty = py - ay; tz = pz - az
    u = (tx * qx + ty * qy + tz * qz) * idet
    if u < -eps or u > 1.0 + eps:
        return -1.0
    rx = ty * e1z - tz * e1y
    ry = tz * e1x - tx * e1z
    rz = tx * e1y - ty * e1x
    v = (dx * rx + dy * ry + dz * rz) * idet
    if v < -eps or u + v > 1.0 + eps:
        return -1.0
    return (e2x * rx + e2y * ry + e2z * rz) * idet


# Feste, "schiefe" Richtungen für Innen/Außen-Tests (vermeiden Treffer auf Kanten)
_PARITY_DIRS = []
for _d in ((0.4152, 0.5717, 0.7077), (-0.6629, 0.3149, 0.6792), (0.2217, -0.8793, -0.4213)):
    _l = math.sqrt(sum(c * c for c in _d))
    _PARITY_DIRS.append(tuple(c / _l for c in _d))
del _d, _l


# ---------------------------------------------------------------------------
# Orientierung / Verschachtelung
# ---------------------------------------------------------------------------

def _patch_stats(topo, vol6, area):
    npatch = topo["npatch"]
    patch = topo["patch"]
    par = topo["par"]
    cnt = [0] * npatch
    c1 = [0] * npatch
    S0 = [0.0] * npatch
    S1 = [0.0] * npatch
    rep = [-1] * npatch          # größtes Dreieck je Patch (Startpunkt für Strahlen)
    rep_a = [-1.0] * npatch
    for g, p in enumerate(patch):
        cnt[p] += 1
        if par[g]:
            c1[p] += 1
            S1[p] += vol6[g]
        else:
            S0[p] += vol6[g]
        if area[g] > rep_a[p]:
            rep_a[p] = area[g]
            rep[p] = g
    return cnt, c1, S0, S1, rep


def _nesting_depths(topo, closed, X, Y, Z, A, B, C, rep, grid) -> Dict[int, int]:
    """Verschachtelungstiefe geschlossener Patches (0 = außen, 1 = Hohlraum, ...)."""
    patch = topo["patch"]
    depth = {p: 0 for p in closed}
    if len(closed) < 2:
        return depth
    # Bounding-Box je Patch über die Eckpunkte
    N = len(X)
    vp = [-1] * N
    for g, p in enumerate(patch):
        vp[A[g]] = p
        vp[B[g]] = p
        vp[C[g]] = p
    inf = math.inf
    bb = {p: [inf, inf, inf, -inf, -inf, -inf] for p in closed}
    for v, p in enumerate(vp):
        b = bb.get(p)
        if b is None:
            continue
        x = X[v]; y = Y[v]; z = Z[v]
        if x < b[0]: b[0] = x
        if y < b[1]: b[1] = y
        if z < b[2]: b[2] = z
        if x > b[3]: b[3] = x
        if y > b[4]: b[4] = y
        if z > b[5]: b[5] = z
    tol = 1e-6
    for i in closed:
        bi = bb[i]
        cands = set()
        for j in closed:
            if j == i:
                continue
            bj = bb[j]
            if (bj[0] - tol <= bi[0] and bj[1] - tol <= bi[1] and bj[2] - tol <= bi[2]
                    and bi[3] <= bj[3] + tol and bi[4] <= bj[4] + tol and bi[5] <= bj[5] + tol):
                cands.add(j)
        if not cands or grid is None:
            continue
        g = rep[i]
        a, b, c = A[g], B[g], C[g]
        px = (X[a] + X[b] + X[c]) / 3.0
        py = (Y[a] + Y[b] + Y[c]) / 3.0
        pz = (Z[a] + Z[b] + Z[c]) / 3.0
        votes: Dict[int, int] = defaultdict(int)
        for dx, dy, dz in _PARITY_DIRS:
            odd: Dict[int, int] = defaultdict(int)
            seen = set()
            for lst, _t in grid.traverse(px, py, pz, dx, dy, dz):
                for h in lst:
                    if h in seen:
                        continue
                    seen.add(h)
                    ph = patch[h]
                    if ph not in cands:
                        continue
                    t = _ray_hit(X, Y, Z, A[h], B[h], C[h], px, py, pz, dx, dy, dz, 0.0)
                    if t > 1e-9:
                        odd[ph] ^= 1
            for j in cands:
                votes[j] += odd.get(j, 0)
        depth[i] = sum(1 for j in cands if votes[j] >= 2)
    return depth


# ---------------------------------------------------------------------------
# Wandstärke
# ---------------------------------------------------------------------------

def _walls(grid, X, Y, Z, A, B, C, area, NX, NY, NZ, fsign, samples, deadline,
           min_wall, rec_wall):
    rng = random.Random(0x5CAD)
    cum = list(itertools.accumulate(area))
    total = cum[-1] if cum else 0.0
    T = len(area)
    res: Dict[str, Any] = {
        "min": None, "at": None, "median": None, "thin_fraction": None,
        "below_recommended_fraction": None, "samples": 0, "misses": 0,
        "requested": samples, "partial": False, "min_wall": min_wall,
        "recommended_wall": rec_wall, "note": WALL_NOTE,
    }
    if total <= 0 or samples <= 0:
        return res
    ext = [(grid.dims[i]) * grid.s for i in range(3)]
    tmax = math.sqrt(ext[0] ** 2 + ext[1] ** 2 + ext[2] ** 2) * 1.01
    thick: List[float] = []
    best = math.inf
    best_at = None
    misses = 0
    rnd = rng.random
    traverse = grid.traverse
    for n in range(samples):
        if (n & 15) == 0 and time.perf_counter() > deadline:
            res["partial"] = True
            break
        f = bisect.bisect_right(cum, rnd() * total)
        if f >= T:
            f = T - 1
        if area[f] <= 0.0:
            continue
        a, b, c = A[f], B[f], C[f]
        r1 = rnd()
        r2 = rnd()
        if r1 + r2 > 1.0:
            r1 = 1.0 - r1
            r2 = 1.0 - r2
        ax = X[a]; ay = Y[a]; az = Z[a]
        px = ax + r1 * (X[b] - ax) + r2 * (X[c] - ax)
        py = ay + r1 * (Y[b] - ay) + r2 * (Y[c] - ay)
        pz = az + r1 * (Z[b] - az) + r2 * (Z[c] - az)
        sg = fsign[f] if fsign is not None else 1.0
        # Strahl entgegen der (korrigierten) Außennormale ins Material
        nx0 = NX[f] * sg
        ny0 = NY[f] * sg
        nz0 = NZ[f] * sg
        dx, dy, dz = -nx0, -ny0, -nz0
        tbest = math.inf
        seen = set()
        for lst, tent in traverse(px, py, pz, dx, dy, dz, tmax):
            if tbest <= tent:
                break
            for h in lst:
                if h in seen:
                    continue
                seen.add(h)
                ha, hb, hc = A[h], B[h], C[h]
                if ha == a or ha == b or ha == c or hb == a or hb == b or hb == c \
                        or hc == a or hc == b or hc == c:
                    continue
                sh = fsign[h] if fsign is not None else 1.0
                # nur Austrittsflächen (Normale grob entgegengesetzt) zählen
                if (NX[h] * nx0 + NY[h] * ny0 + NZ[h] * nz0) * sh >= 0.0:
                    continue
                t = _ray_hit(X, Y, Z, ha, hb, hc, px, py, pz, dx, dy, dz, 1e-7)
                if 1e-4 < t < tbest:
                    tbest = t
        if tbest == math.inf:
            misses += 1
            continue
        thick.append(tbest)
        if tbest < best:
            best = tbest
            best_at = (px, py, pz)
    res["misses"] = misses
    res["samples"] = len(thick)
    if thick:
        thick.sort()
        k = len(thick)
        res["min"] = round(best, 3)
        res["at"] = [round(v, 2) for v in best_at]
        res["median"] = round(thick[k // 2], 3)
        res["thin_fraction"] = round(bisect.bisect_left(thick, min_wall) / k, 4)
        res["below_recommended_fraction"] = round(bisect.bisect_left(thick, rec_wall) / k, 4)
    return res


# ---------------------------------------------------------------------------
# Druckbett
# ---------------------------------------------------------------------------

def _hull2d(points: Iterable[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Konvexe Hülle (Andrew's Monotone Chain)."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _fit_rotation(hull, bx, by) -> Tuple[Optional[float], float]:
    """Drehwinkel (Grad, 0..90) um die Hochachse mit dem meisten Rand zum Bett.

    Gibt (winkel, rand_mm) zurück; winkel ist None, wenn es nie passt.
    """
    if not hull:
        return None, -math.inf
    angles = {i * 0.5 for i in range(181)}
    n = len(hull)
    for i in range(n):
        x0, y0 = hull[i]
        x1, y1 = hull[(i + 1) % n]
        a = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 90.0
        angles.add(round(a, 4))
        angles.add(round((90.0 - a) % 90.0, 4))
    best_deg: Optional[float] = None
    best_margin = -math.inf
    for deg in sorted(angles, key=lambda d: min(d, 90.0 - d)):
        th = math.radians(deg)
        c, s = math.cos(th), math.sin(th)
        us = [x * c - y * s for x, y in hull]
        vs = [x * s + y * c for x, y in hull]
        w = max(us) - min(us)
        h = max(vs) - min(vs)
        m = max(min(bx - w, by - h), min(bx - h, by - w))
        if m > best_margin + 1e-9:
            best_margin = m
            best_deg = deg
    if best_margin < -1e-6:
        return None, best_margin
    return best_deg, best_margin


_AXIS = "XYZ"
_GOOD_MARGIN = 5.0   # mm Rand, damit Skirt/Brim noch Platz haben


def _bed_check(size, bed, X, Y, Z) -> Dict[str, Any]:
    """Passt das Teil aufs Bett – so, um Z gedreht, diagonal oder gekippt?"""
    bx, by, bz = bed
    sx, sy, sz = size
    eps = 1e-6
    res: Dict[str, Any] = {"fits": False, "fits_rotated": False, "bed": [bx, by, bz],
                           "orientation": None, "rotation_deg": None, "hint": None}

    def margin(a, b, c):
        return min(bx - a, by - b, bz - c)

    if margin(sx, sy, sz) >= -eps:
        res.update(fits=True, fits_rotated=True, orientation="original")
        return res
    # Kandidaten: (Priorität, Rand, Ausrichtung, Hinweis, Winkel)
    # Priorität: Drehungen um Z behalten die geplante Druckausrichtung -> zuerst
    cands: List[Tuple[int, float, str, str, Optional[float]]] = []
    m = margin(sy, sx, sz)
    if m >= -eps:
        cands.append((1, m, "rot_z_90", "passt, wenn es um 90° um die Z-Achse gedreht wird",
                      90.0))
    for up in (0, 1):
        u, v = [i for i in range(3) if i != up]
        m = max(margin(size[u], size[v], size[up]), margin(size[v], size[u], size[up]))
        if m >= -eps:
            cands.append((3, m, f"up_{_AXIS[up].lower()}",
                          f"passt, wenn es gekippt wird (bisherige {_AXIS[up]}-Richtung "
                          "zeigt nach oben)", None))
    coords = (X, Y, Z)
    for up in (2, 0, 1):
        if size[up] > bz + eps:
            continue
        if up != 2 and any(c[1] >= -eps for c in cands):
            continue   # gekippt + schräg nur als letzte Möglichkeit
        u, v = [i for i in range(3) if i != up]
        # schräg/diagonal aufs Bett legen (konvexe Hülle der Grundfläche drehen)
        deg, m = _fit_rotation(_hull2d(zip(coords[u], coords[v])), bx, by)
        if deg is None:
            continue
        m = min(m, bz - size[up])
        if up == 2:
            cands.append((2, m, "rot_z",
                          f"passt diagonal, wenn es um ca. {deg:.0f}° um die Z-Achse "
                          "gedreht wird", round(deg, 1)))
        else:
            cands.append((4, m, f"up_{_AXIS[up].lower()}_rot",
                          f"passt, wenn es gekippt ({_AXIS[up]} nach oben) und dann um ca. "
                          f"{deg:.0f}° gedreht wird", round(deg, 1)))
    if cands:
        prio, m, orient, hint, deg = min(cands, key=lambda c: (c[1] < _GOOD_MARGIN, c[0]))
        if m < _GOOD_MARGIN:
            hint += f" (nur {_fmt(max(m, 0.0), 1)} mm Rand)"
        res.update(fits_rotated=True, orientation=orient, rotation_deg=deg, hint=hint)
    return res


# ---------------------------------------------------------------------------
# Hilfsfunktionen für Texte
# ---------------------------------------------------------------------------

def _fmt(v: float, nd: int = 2) -> str:
    s = f"{v:.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _dims(vals: Sequence[float]) -> str:
    return " × ".join(_fmt(v) for v in vals) + " mm"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _issue(level: str, text: str, code: str) -> Dict[str, str]:
    return {"level": level, "text": text, "code": code}


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------

def analyze(path_or_mesh: Union[str, os.PathLike, Mesh], *,
            bed: Sequence[float] = DEFAULT_BED, min_wall: float = 0.8,
            recommended_wall: float = 1.2, wall_samples: int = 2000,
            time_budget: float = 8.0) -> Dict[str, Any]:
    """Prüft ein STL-Modell und gibt ein JSON-fähiges dict zurück.

    ``path_or_mesh``: Pfad zu einer STL-Datei oder ein :class:`Mesh`.
    ``bed``: Druckraum X, Y, Z in mm.  ``min_wall``: darunter Warnung
    (Standard 0.8 mm = 2 Linienbreiten).  ``recommended_wall``: darunter Hinweis.
    ``wall_samples``: Anzahl Messpunkte für die Wandstärke (0 = aus).
    ``time_budget``: Zeitbudget (s) ab Aufruf. Ist es verbraucht, bricht die
    Wandstärken-Messung ab und liefert ein Teilergebnis (``walls.partial``);
    ihr bleiben aber mindestens 15 % des Budgets. Einlesen, Topologie und
    Gitteraufbau laufen immer vollständig (bei 300 000 Dreiecken ca. 3–4 s).

    Unlesbare Dateien lösen ``ValueError`` aus (deutsche Meldung).
    """
    t_start = time.perf_counter()
    timings: Dict[str, float] = {}
    bed = tuple(float(v) for v in bed)
    if isinstance(path_or_mesh, Mesh):
        mesh = path_or_mesh
    else:
        mesh = read_stl(path_or_mesh)
    timings["read"] = time.perf_counter() - t_start

    V = mesh.vertices
    X = V[0::3].tolist()
    Y = V[1::3].tolist()
    Z = V[2::3].tolist()
    tri = mesh.triangles
    A = tri[0::3].tolist()
    B = tri[1::3].tolist()
    C = tri[2::3].tolist()
    T_file = len(A)

    result: Dict[str, Any] = {
        "file": mesh.source or None,
        "format": mesh.fmt or None,
        "triangles": T_file,
        "vertices": len(X),
    }
    issues: List[Dict[str, str]] = []
    for note in mesh.notes:
        issues.append(_issue("info", note, "file_note"))

    # zusammengefallene Dreiecke (zwei gleiche Eckpunkte) aus der Topologie nehmen
    collapsed = 0
    if T_file:
        keep = [f for f, (a, b, c) in enumerate(zip(A, B, C)) if a != b and b != c and a != c]
        collapsed = T_file - len(keep)
        if collapsed:
            file_n = mesh.file_normals
            A = [A[f] for f in keep]
            B = [B[f] for f in keep]
            C = [C[f] for f in keep]
            if file_n is not None and len(file_n) == 3 * T_file:
                fn = array("f")
                for f in keep:
                    fn.extend(file_n[3 * f:3 * f + 3])
                file_normals = fn
            else:
                file_normals = None
        else:
            file_normals = mesh.file_normals
        del keep
    else:
        file_normals = None
    T = len(A)

    if T == 0:
        result.update({
            "bbox_min": [0.0, 0.0, 0.0], "bbox_max": [0.0, 0.0, 0.0], "size": [0.0, 0.0, 0.0],
            "volume_cm3": 0.0, "signed_volume_cm3": 0.0, "area_cm2": 0.0, "shells": 0,
            "bodies": 0, "cavities": 0, "watertight": False, "open_edges": 0,
            "nonmanifold_edges": 0, "misoriented_edges": 0, "orientation_consistent": True,
            "flipped_faces_estimate": 0, "inverted": False, "inverted_shells": 0,
            "degenerate_triangles": collapsed, "stored_normals_mismatch": 0,
            "base_area_mm2": 0.0, "overhang_area_cm2": 0.0, "overhang_fraction": 0.0,
            "bed": {"fits": False, "fits_rotated": False, "bed": list(bed),
                    "orientation": None, "rotation_deg": None, "hint": None},
            "walls": None,
        })
        issues.append(_issue("error", "STL enthält keine (gültigen) Dreiecke – das Modell "
                                      "ist leer. Wurde in OpenSCAD überhaupt etwas erzeugt?",
                             "empty"))
        return _finish(result, issues, t_start, timings)

    # --- Maße -------------------------------------------------------------
    used = set(A)
    used.update(B)
    used.update(C)
    if len(used) != len(X):
        Xu = [X[i] for i in used]
        Yu = [Y[i] for i in used]
        Zu = [Z[i] for i in used]
    else:
        Xu, Yu, Zu = X, Y, Z
    bmin = [min(Xu), min(Yu), min(Zu)]
    bmax = [max(Xu), max(Yu), max(Zu)]
    del used
    size = [bmax[i] - bmin[i] for i in range(3)]
    ref = [(bmin[i] + bmax[i]) * 0.5 for i in range(3)]
    result["bbox_min"] = [round(v, 3) for v in bmin]
    result["bbox_max"] = [round(v, 3) for v in bmax]
    result["size"] = [round(v, 2) for v in size]

    # --- Geometrie ---------------------------------------------------------
    t0 = time.perf_counter()
    area, NX, NY, NZ, vol6, degenerate = _geometry(X, Y, Z, A, B, C, ref)
    total_area = math.fsum(area)
    signed_vol = math.fsum(vol6) / 6.0
    timings["geometry"] = time.perf_counter() - t0

    # --- Topologie ---------------------------------------------------------
    t0 = time.perf_counter()
    topo = _topology(A, B, C, len(X))
    cnt, c1, S0, S1, rep = _patch_stats(topo, vol6, area)
    npatch = topo["npatch"]
    closed = [p for p in range(npatch) if not topo["patch_open"][p]]
    timings["topology"] = time.perf_counter() - t0

    # Gitter nur bauen, wenn Wandstärke oder Hohlraum-Prüfung es braucht
    grid: Optional[_Grid] = None
    need_nesting = 2 <= len(closed) <= _NESTING_MAX_SHELLS
    if (wall_samples > 0 or need_nesting) and total_area > 0:
        t0 = time.perf_counter()
        grid = _Grid(X, Y, Z, A, B, C, area, NX, NY, NZ, bmin, bmax)
        timings["grid"] = time.perf_counter() - t0

    # --- Orientierung je Patch festlegen ----------------------------------
    t0 = time.perf_counter()
    nesting_known = len(closed) <= _NESTING_MAX_SHELLS and (len(closed) < 2 or grid is not None)
    depth: Dict[int, int] = {}
    if nesting_known:
        depth = _nesting_depths(topo, closed, X, Y, Z, A, B, C, rep, grid)
    wrong_par = [0] * npatch     # Parität der falsch orientierten Dreiecke je Patch
    flipped = 0
    inverted_shells = 0
    cavities = 0
    corr_vol6 = 0.0
    for p in range(npatch):
        v_as0 = S0[p] - S1[p]          # Volumen*6, wenn Parität 0 "richtig" ist
        is_closed = not topo["patch_open"][p]
        if is_closed and nesting_known and abs(v_as0) > 1e-12:
            d = depth.get(p, 0)
            want_pos = (d % 2 == 0)
            if d % 2 == 1:
                cavities += 1
            wp = 1 if (v_as0 > 0) == want_pos else 0
        else:
            c0 = cnt[p] - c1[p]
            if c0 != c1[p]:
                wp = 1 if c0 > c1[p] else 0
            else:
                wp = 1 if v_as0 >= 0 else 0
        wrong_par[p] = wp
        wrong = c1[p] if wp == 1 else cnt[p] - c1[p]
        corr_vol6 += v_as0 if wp == 1 else -v_as0
        if is_closed and nesting_known and wrong == cnt[p] and cnt[p] > 0:
            inverted_shells += 1
        else:
            flipped += wrong
    fsign: Optional[List[float]] = None
    if flipped or inverted_shells:
        patch = topo["patch"]
        par = topo["par"]
        fsign = [-1.0 if par[g] == wrong_par[patch[g]] else 1.0 for g in range(T)]
    timings["orientation"] = time.perf_counter() - t0

    result["volume_cm3"] = round(abs(corr_vol6) / 6000.0, 3)
    result["signed_volume_cm3"] = round(signed_vol / 1000.0, 3)
    result["area_cm2"] = round(total_area / 100.0, 3)
    result["shells"] = topo["shells"]
    result["cavities"] = cavities
    result["bodies"] = max(topo["shells"] - cavities, 0)
    result["watertight"] = topo["open_edges"] == 0 and topo["nonmanifold_edges"] == 0
    result["open_edges"] = topo["open_edges"]
    result["nonmanifold_edges"] = topo["nonmanifold_edges"]
    result["misoriented_edges"] = topo["misoriented_edges"]
    result["orientation_consistent"] = topo["misoriented_edges"] == 0 and topo["conflicts"] == 0
    result["flipped_faces_estimate"] = flipped
    result["inverted"] = signed_vol < 0
    result["inverted_shells"] = inverted_shells if nesting_known else None
    result["degenerate_triangles"] = degenerate + collapsed

    # gespeicherte Normalen vs. Umlaufsinn
    mismatch = 0
    if file_normals is not None and len(file_normals) == 3 * T:
        fnx = file_normals[0::3]
        fny = file_normals[1::3]
        fnz = file_normals[2::3]
        mismatch = sum(1 for g in range(T)
                       if fnx[g] * NX[g] + fny[g] * NY[g] + fnz[g] * NZ[g] < -1e-3)
    result["stored_normals_mismatch"] = mismatch

    # --- Auflagefläche und Überhänge (mit korrigierten Normalen) -----------
    zmin = bmin[2]
    ztol = 0.02
    cos45 = math.sqrt(0.5)
    base_area = 0.0
    overhang = 0.0
    for g in range(T):
        ar = area[g]
        if ar <= 0.0:
            continue
        nz = NZ[g] if fsign is None else NZ[g] * fsign[g]
        if nz >= -cos45:
            continue
        zlo = min(Z[A[g]], Z[B[g]], Z[C[g]])
        if zlo <= zmin + ztol:
            if nz < -0.999 and max(Z[A[g]], Z[B[g]], Z[C[g]]) <= zmin + ztol:
                base_area += ar
            continue
        overhang += ar
    result["base_area_mm2"] = round(base_area, 2)
    result["overhang_area_cm2"] = round(overhang / 100.0, 3)
    result["overhang_fraction"] = round(overhang / total_area, 4) if total_area > 0 else 0.0

    # --- Druckbett ------------------------------------------------------------
    result["bed"] = _bed_check(size, bed, Xu, Yu, Zu)

    # --- Wandstärke ----------------------------------------------------------
    if grid is not None and wall_samples > 0:
        t0 = time.perf_counter()
        deadline = max(t_start + time_budget, t0 + 0.15 * time_budget)
        result["walls"] = _walls(grid, X, Y, Z, A, B, C, area, NX, NY, NZ, fsign,
                                 int(wall_samples), deadline, min_wall, recommended_wall)
        timings["walls"] = time.perf_counter() - t0
    else:
        result["walls"] = None

    _collect_issues(result, issues, size, bmin, bed, min_wall, recommended_wall, T)
    return _finish(result, issues, t_start, timings)


def _finish(result, issues, t_start, timings):
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: order.get(i["level"], 3))
    result["issues"] = issues
    result["ok"] = not any(i["level"] == "error" for i in issues)
    result["seconds"] = round(time.perf_counter() - t_start, 3)
    result["timings"] = {k: round(v, 3) for k, v in timings.items()}
    return result


def _collect_issues(r, issues, size, bmin, bed, min_wall, rec_wall, T):
    add = issues.append
    # --- Netz ---------------------------------------------------------------
    if r["open_edges"]:
        add(_issue("error", f"Netz nicht geschlossen: {r['open_edges']} offene Kanten – "
                            "Wände fehlen oder Löcher im Modell.", "open_edges"))
    if r["nonmanifold_edges"]:
        add(_issue("warning",
                   f"{_plural(r['nonmanifold_edges'], 'Kante', 'Kanten')} mit mehr als zwei "
                   "Flächen (nicht-mannigfaltig) – z. B. Körper, die sich nur an einer Kante "
                   "berühren. Slicer reparieren das meist; sicherer ist eine kleine Überlappung.",
                   "nonmanifold"))
    if r["inverted"]:
        add(_issue("error", "Modell ist innen-außen vertauscht (Normalen zeigen nach innen, "
                            "Volumen negativ).", "inverted"))
    elif r.get("inverted_shells"):
        add(_issue("warning",
                   f"{_plural(r['inverted_shells'], 'Teil ist', 'Teile sind')} innen-außen "
                   "vertauscht (Normalen zeigen nach innen) – im Slicer ggf. reparieren.",
                   "inverted_shells"))
    if r["flipped_faces_estimate"]:
        n = r["flipped_faces_estimate"]
        add(_issue("warning", f"{_plural(n, 'Dreieck', 'Dreiecke')} falsch herum orientiert "
                              "(Normalen) – im Slicer ggf. reparieren.", "flipped"))
    elif not r["orientation_consistent"]:
        add(_issue("warning", "Umlaufsinn der Dreiecke ist nicht einheitlich (Normalen) – "
                              "im Slicer ggf. reparieren.", "orientation"))
    if r["degenerate_triangles"]:
        n = r["degenerate_triangles"]
        lvl = "warning" if n > max(50, 0.05 * T) else "info"
        add(_issue(lvl, f"{_plural(n, 'entartetes Dreieck', 'entartete Dreiecke')} "
                        "(Fläche null) – meist unkritisch, Slicer ignorieren sie.", "degenerate"))
    if r["stored_normals_mismatch"] and not r["flipped_faces_estimate"] and not r["inverted"]:
        add(_issue("info", f"{r['stored_normals_mismatch']} gespeicherte Normalen passen nicht "
                           "zur Eckpunkt-Reihenfolge – Slicer berechnen sie meist neu.",
                   "stored_normals"))
    bodies = r.get("bodies") or r["shells"]
    if bodies > 1:
        add(_issue("info", f"Modell besteht aus {bodies} getrennten Teilen.", "shells"))
    if r.get("cavities"):
        n = r["cavities"]
        add(_issue("info", f"Modell enthält {_plural(n, 'geschlossenen Hohlraum', 'geschlossene Hohlräume')} "
                           "(innen leer, ohne Öffnung) – gewollt?", "cavities"))

    # --- Maße / Druckbett ----------------------------------------------------
    bedr = r["bed"]
    if not bedr["fits"]:
        base = (f"Teil passt nicht aufs Druckbett ({_dims(size)} > "
                f"{_dims(bed)})")
        if bedr["fits_rotated"]:
            add(_issue("warning", f"{base}, {bedr['hint']}.", "bed_rotate"))
        else:
            unit = ""
            if max(size) > 10 * max(bed):
                unit = " Falsche Einheit (cm statt mm)?"
            add(_issue("error", f"{base} – verkleinern oder in Teile zerlegen.{unit}", "bed"))
    elif size[0] > bed[0] - 5 or size[1] > bed[1] - 5:
        add(_issue("info", "Teil nutzt fast das ganze Druckbett – Skirt/Brim ggf. abschalten "
                           "oder Teil drehen.", "bed_edge"))
    if max(size) < 1.0:
        add(_issue("warning", f"Modell ist sehr klein ({_dims(size)}) – falsche Einheit "
                              "(z. B. cm oder m statt mm)?", "tiny"))
    zmin = bmin[2]
    if abs(zmin) > 0.01:
        where = "schwebt über dem Bett" if zmin > 0 else "ragt unter die Bettebene"
        add(_issue("info", f"Modell liegt nicht auf z=0 (unterste Stelle z={_fmt(zmin)} mm, "
                           f"{where}) – der Slicer legt es normalerweise "
                           "automatisch auf.", "z0"))

    # --- Auflage / Überhänge -------------------------------------------------
    base = r["base_area_mm2"]
    if base < 1.0:
        add(_issue("warning", f"Modell berührt das Bett kaum (Auflagefläche {_fmt(base)} mm²) – "
                              "Kippgefahr/schlechte Haftung. Flache Seite nach unten drehen oder "
                              "Stützen/Brim verwenden.", "base"))
    elif size[2] > 15 and base < 25 and base < 0.01 * size[2] ** 2:
        add(_issue("info", f"Sehr kleine Grundfläche ({_fmt(base)} mm²) bei {_fmt(size[2])} mm "
                           "Höhe – Kippgefahr / Haftung, Brim empfohlen.", "base_small"))
    if r["overhang_area_cm2"] * 100 > 10 and r["overhang_fraction"] > 0.005:
        add(_issue("info", f"Ca. {_fmt(r['overhang_fraction'] * 100, 1)} % der Oberfläche "
                           "hängen stärker als 45° über – Stützstrukturen nötig oder Teil "
                           "anders ausrichten.", "overhang"))

    # --- Wandstärke ----------------------------------------------------------
    w = r.get("walls")
    if w and w.get("min") is not None:
        wmin = w["min"]
        frac = w["thin_fraction"] or 0.0
        pct = _fmt(frac * 100, 1)
        nozzle = min_wall / 2.0
        lines = min_wall / nozzle
        if wmin < min_wall:
            # Gewindezähne, Spitzen und scharfe Kanten sind naturgemäß dünn –
            # erst ab 1,5 % der Oberfläche eine Warnung
            spots = frac < 0.015
            lvl = "info" if spots else "warning"
            where = (" (nur vereinzelte Stellen, z. B. Gewindespitzen, Spitzen oder scharfe Kanten)" if spots
                     else f" (betrifft ca. {pct} % der Oberfläche)")
            if wmin < nozzle:
                text = (f"Dünnste Wand ca. {_fmt(wmin)} mm – dünner als eine Linienbreite "
                        f"({_fmt(nozzle)} mm), wird vermutlich gar nicht gedruckt{where}.")
            else:
                text = (f"Dünnste Wand ca. {_fmt(wmin)} mm – unter {_fmt(min_wall)} mm "
                        f"({_fmt(lines, 0)} Linienbreiten) wird sie evtl. nicht gedruckt{where}.")
            add(_issue(lvl, text, "thin_wall"))
        elif wmin < rec_wall:
            add(_issue("info", f"Dünnste Wand ca. {_fmt(wmin)} mm – ab {_fmt(rec_wall)} mm "
                               "wird das Teil stabiler.", "wall_recommended"))
    if w and w.get("partial"):
        add(_issue("info", f"Wandstärken-Prüfung wegen Zeitlimit nur teilweise "
                           f"({w['samples'] + w['misses']} von {w['requested']} Messpunkten).",
                   "walls_partial"))


# ---------------------------------------------------------------------------
# Kurzfassung
# ---------------------------------------------------------------------------

def summary_text(result: Dict[str, Any]) -> str:
    """Kurze deutsche Zusammenfassung (mehrzeilig) für Logs/Konsole."""
    r = result
    lines = []
    status = "OK" if r.get("ok") else "FEHLER"
    name = os.path.basename(r["file"]) if r.get("file") else "Modell"
    lines.append(f"STL-Prüfung {name}: {status}")
    lines.append(f"Maße: {_dims(r.get('size', [0, 0, 0]))}  |  Volumen "
                 f"{r.get('volume_cm3', 0):.2f} cm³  |  Oberfläche {r.get('area_cm2', 0):.2f} cm²")
    parts = []
    if r.get("open_edges"):
        parts.append(_plural(r["open_edges"], "offene Kante", "offene Kanten"))
    if r.get("nonmanifold_edges"):
        parts.append(_plural(r["nonmanifold_edges"], "nicht-mannigfaltige Kante",
                             "nicht-mannigfaltige Kanten"))
    net = ", ".join(parts) if parts else ("geschlossen" if r.get("watertight") else "offen")
    if r.get("inverted"):
        norm = "innen-außen vertauscht"
    elif r.get("flipped_faces_estimate"):
        norm = f"{r['flipped_faces_estimate']} Dreiecke falsch herum"
    else:
        norm = "Normalen ok"
    lines.append(f"Netz: {r.get('triangles', 0)} Dreiecke, {r.get('vertices', 0)} Eckpunkte, "
                 f"{_plural(r.get('bodies') or r.get('shells', 0), 'Teil', 'Teile')} – {net}, {norm}")
    bedr = r.get("bed") or {}
    if bedr:
        if bedr.get("fits"):
            bt = "passt"
        elif bedr.get("fits_rotated"):
            bt = bedr.get("hint") or "passt gedreht"
        else:
            bt = "passt NICHT"
        lines.append(f"Druckbett {_dims(bedr.get('bed', DEFAULT_BED))}: {bt}")
    w = r.get("walls")
    if w and w.get("min") is not None:
        lines.append(f"Wandstärke (Schätzung, {w['samples']} Messpunkte): min. ca. "
                     f"{_fmt(w['min'])} mm, Median {_fmt(w['median'])} mm, "
                     f"unter {_fmt(w['min_wall'])} mm: {_fmt((w['thin_fraction'] or 0) * 100, 1)} %")
    labels = {"error": "FEHLER", "warning": "WARNUNG", "info": "Hinweis"}
    for i in r.get("issues", []):
        lines.append(f"[{labels.get(i['level'], i['level'])}] {i['text']}")
    lines.append(f"Analysezeit: {r.get('seconds', 0):.2f} s")
    return "\n".join(lines)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="STL-Qualitätsprüfung (SCAD Studio)")
    ap.add_argument("stl", nargs="+")
    ap.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    ap.add_argument("--samples", type=int, default=2000)
    args = ap.parse_args(argv)
    rc = 0
    for p in args.stl:
        try:
            res = analyze(p, wall_samples=args.samples)
        except (ValueError, OSError) as exc:
            print(f"{p}: {exc}")
            rc = 2
            continue
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else summary_text(res))
        if not res["ok"]:
            rc = max(rc, 1)
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
