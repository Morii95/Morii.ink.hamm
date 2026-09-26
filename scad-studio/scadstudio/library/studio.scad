// ===========================================================================
//  studio.scad – Verbindungs- und Druckbibliothek für SCAD Studio
//  Version 1.0.0
//
//  MIT License
//
//  Copyright (c) 2026 SCAD Studio contributors
//
//  Permission is hereby granted, free of charge, to any person obtaining a
//  copy of this software and associated documentation files (the
//  "Software"), to deal in the Software without restriction, including
//  without limitation the rights to use, copy, modify, merge, publish,
//  distribute, sublicense, and/or sell copies of the Software, and to permit
//  persons to whom the Software is furnished to do so, subject to the
//  following conditions:
//
//  The above copyright notice and this permission notice shall be included
//  in all copies or substantial portions of the Software.
//
//  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
//  OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
//  MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
//  NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
//  DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
//  OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
//  USE OR OTHER DEALINGS IN THE SOFTWARE.
// ---------------------------------------------------------------------------
//  Einbinden:   use <studio.scad>
//  Die Datei enthält nur Module und Funktionen (keine Geometrie auf oberster
//  Ebene). Läuft mit OpenSCAD 2021.01 und neueren Versionen (Manifold).
//
//  Konventionen
//  * Einheiten: mm und Grad.
//  * Positive Teile (Bolzen, Zapfen, Stecker, Außengewinde) stehen auf z=0,
//    wachsen nach +Z und sind auf der Z-Achse zentriert.
//  * Negative Teile (Löcher, Taschen, Innengewinde, Schlitze) sind für
//    difference() gedacht. Sie beginnen ebenfalls bei z=0 (Eintrittsfläche)
//    und gehen nach +Z; zusätzlich ragen sie um `ext` (0.05 mm) unter z=0,
//    damit sie sauber schneiden. flip=true dreht sie um 180° um die X-Achse
//    (sie gehen dann von z=0 nach -Z, zum Schneiden in eine Oberseite).
//  * Buchsen/Innenteile enthalten das Spaltmaß automatisch; Stecker/Bolzen
//    sind nominal. Gleiche Nennmaße an beide Seiten übergeben.
//  * Globales Spaltmaß: $tol (Standard 0.2 mm radial je Seite),
//    Düsendurchmesser: $nozzle (Standard 0.4 mm).
//  * Auflösung über $fn/$fa/$fs. Sind $fa/$fs auf den OpenSCAD-Standard-
//    werten und $fn=0, nimmt die Bibliothek feinere Werte ($fa=4, $fs=0.4).
// ===========================================================================

function studio_version() = "1.0.0";

// ---------------------------------------------------------------------------
// 0. Interne Helfer
// ---------------------------------------------------------------------------

// Spaltmaß: Parameter > $tol > 0.2 (is_undef erzeugt keine Warnung,
// auch wenn $tol nirgends definiert ist)
function _tol(t)   = !is_undef(t) ? t : (!is_undef($tol) ? $tol : 0.2);
function _nozzle() = !is_undef($nozzle) ? $nozzle : 0.4;
function _ext(e)   = !is_undef(e) ? e : 0.05;
function _v2(s)    = is_list(s) ? [s[0], s[1]] : [s, s];

// Segmentanzahl für Kreise wie in OpenSCAD, mit feinerem Standard
function _frags(r) =
    $fn > 0 ? max(3, floor($fn)) :
    let(dflt = ($fa == 12 && $fs == 2),
        fa = dflt ? 4 : $fa,
        fs = dflt ? 0.4 : $fs)
    max(5, ceil(min(360 / fa, r * 2 * PI / fs)));

// Umschriebenes Vieleck: der wahre Kreis mit Radius r liegt vollständig
// innerhalb (Löcher werden nie zu klein, egal welches $fn).
function _hole_r(r, n) = r / cos(180 / n);

module _hole_cyl(r, h, n) {
    nn = is_undef(n) ? _frags(r) : n;
    cylinder(r = _hole_r(r, nn), h = h, $fn = nn);
}

// 45°-Einlauffase eines Lochs (Radius r) an der Eintrittsfläche z=0
module _entry_cone(r, c, e, n) {
    if (c > 0) {
        nn = is_undef(n) ? _frags(r) : n;
        translate([0, 0, -e])
            cylinder(r1 = _hole_r(r + c + e, nn), r2 = _hole_r(r - 0.1, nn),
                     h = c + e + 0.1, $fn = nn);
    }
}

// flip: 180° um X drehen (Drehung statt Spiegelung – Gewinde behalten
// dadurch ihre Steigungsrichtung)
module _flip(f) {
    if (f) rotate([180, 0, 0]) children();
    else children();
}

// Tabellenzeile: exakter Treffer oder nächstkleinerer Eintrag
function _row(tab, d) =
    let(i = [for (k = [0 : len(tab) - 1]) if (tab[k][0] <= d + 1e-6) k])
    len(i) == 0 ? tab[0] : tab[i[len(i) - 1]];

// Spalte c einer Tabelle als Lookup-Liste (lineare Interpolation)
function _col(tab, c) = [for (r = tab) [r[0], r[c]]];

// Tabellenwert Spalte c für d: interpoliert, außerhalb proportional zu d
function _lk(tab, c, d) =
    let(first = tab[0], last = tab[len(tab) - 1])
    d > last[0] ? last[c] * d / last[0] :
    d < first[0] ? first[c] * d / first[0] :
    lookup(d, _col(tab, c));

// ---------------------------------------------------------------------------
// 1. Normtabellen (als Funktionen)
// ---------------------------------------------------------------------------

// ISO 261/262 Regelgewinde: [d, Steigung]
function _t_pitch() = [
    [1, 0.25], [1.2, 0.25], [1.4, 0.3], [1.6, 0.35], [2, 0.4], [2.5, 0.45],
    [3, 0.5], [3.5, 0.6], [4, 0.7], [5, 0.8], [6, 1], [7, 1], [8, 1.25],
    [10, 1.5], [12, 1.75], [14, 2], [16, 2], [18, 2.5], [20, 2.5],
    [22, 2.5], [24, 3], [27, 3], [30, 3.5], [33, 3.5], [36, 4], [39, 4],
    [42, 4.5], [45, 4.5], [48, 5], [52, 5], [56, 5.5], [60, 5.5], [64, 6]];

// ISO 273 Durchgangslöcher: [d, fein, mittel, grob]
function _t_clear() = [
    [1.6, 1.7, 1.8, 2.0], [2, 2.2, 2.4, 2.6], [2.5, 2.7, 2.9, 3.1],
    [3, 3.2, 3.4, 3.6], [3.5, 3.7, 3.9, 4.2], [4, 4.3, 4.5, 4.8],
    [5, 5.3, 5.5, 5.8], [6, 6.4, 6.6, 7], [8, 8.4, 9, 10], [10, 10.5, 11, 12],
    [12, 13, 13.5, 14.5], [14, 15, 15.5, 16.5], [16, 17, 17.5, 18.5],
    [18, 19, 20, 21], [20, 21, 22, 24], [22, 23, 24, 26], [24, 25, 26, 28],
    [27, 28, 30, 32], [30, 31, 33, 35], [33, 34, 36, 38], [36, 37, 39, 42]];

// ISO 4032 Sechskantmutter: [d, Schlüsselweite s, Höhe m]
function _t_nut() = [
    [1.6, 3.2, 1.3], [2, 4, 1.6], [2.5, 5, 2], [3, 5.5, 2.4], [3.5, 6, 2.8],
    [4, 7, 3.2], [5, 8, 4.7], [6, 10, 5.2], [8, 13, 6.8], [10, 16, 8.4],
    [12, 18, 10.8], [14, 21, 12.8], [16, 24, 14.8], [18, 27, 15.8],
    [20, 30, 18], [22, 34, 19.4], [24, 36, 21.5], [27, 41, 23.8],
    [30, 46, 25.6], [33, 50, 28.7], [36, 55, 31]];

// ISO 4762 Zylinderkopf: [d, dk, k, Innensechskant s, Tiefe t]
function _t_socket() = [
    [1.6, 3, 1.6, 1.5, 0.7], [2, 3.8, 2, 1.5, 1], [2.5, 4.5, 2.5, 2, 1.1],
    [3, 5.5, 3, 2.5, 1.3], [4, 7, 4, 3, 2], [5, 8.5, 5, 4, 2.5],
    [6, 10, 6, 5, 3], [8, 13, 8, 6, 4], [10, 16, 10, 8, 5],
    [12, 18, 12, 10, 6], [14, 21, 14, 12, 7], [16, 24, 16, 14, 8],
    [20, 30, 20, 17, 10], [24, 36, 24, 19, 12], [30, 45, 30, 22, 15.5],
    [36, 54, 36, 27, 19]];

// DIN 974-1 Senkungsdurchmesser für ISO 4762: [d, D]
function _t_cbore() = [
    [1.6, 3.5], [2, 4.4], [2.5, 5.5], [3, 6.5], [4, 8], [5, 10], [6, 11],
    [8, 15], [10, 18], [12, 20], [14, 24], [16, 26], [20, 33], [24, 40],
    [30, 48], [36, 57]];

// ISO 10642 Senkkopf 90° (theoretischer Kopf-Ø): [d, dk, k, s]
function _t_csk() = [
    [2, 4.4, 1.2, 1.3], [2.5, 5.5, 1.5, 1.5], [3, 6.72, 1.86, 2],
    [4, 8.96, 2.48, 2.5], [5, 11.2, 3.1, 3], [6, 13.44, 3.72, 4],
    [8, 17.92, 4.96, 5], [10, 22.4, 6.2, 6], [12, 26.88, 7.44, 8],
    [16, 33.6, 8.8, 10], [20, 40.32, 10.16, 12]];

// ISO 7380 Linsenkopf: [d, dk, k, s]
function _t_button() = [
    [3, 5.7, 1.65, 2], [4, 7.6, 2.2, 2.5], [5, 9.5, 2.75, 3],
    [6, 10.5, 3.3, 4], [8, 14, 4.4, 5], [10, 17.5, 5.5, 6],
    [12, 21, 6.6, 8], [16, 28, 8.8, 10]];

// ISO 4017 Sechskantkopf: [d, s, k]
function _t_hexhead() = [
    [2, 4, 1.4], [2.5, 5, 1.7], [3, 5.5, 2], [4, 7, 2.8], [5, 8, 3.5],
    [6, 10, 4], [8, 13, 5.3], [10, 16, 6.4], [12, 18, 7.5], [14, 21, 8.8],
    [16, 24, 10], [20, 30, 12.5], [24, 36, 15], [30, 46, 18.7],
    [36, 55, 22.5]];

// Gewindeeinsätze (Ruthex / CNC Kitchen Standard): [d, Loch-Ø, Länge]
function _t_heatset() = [
    [2, 3.2, 4.0], [2.5, 4.0, 5.7], [3, 4.0, 5.7], [4, 5.6, 8.1],
    [5, 6.4, 9.5], [6, 8.0, 12.7], [8, 9.7, 12.7]];

// Öffentliche Tabellenfunktionen ------------------------------------------

// ISO-Regelgewinde-Steigung (M1–M64); unbekannte d: nächstkleinere Norm
function iso_pitch(d) = d < 1 ? max(0.1, 0.25 * d) : _row(_t_pitch(), d)[1];

// ISO 273 Durchgangsloch-Ø; fit = "fine" | "medium" | "coarse"
function iso_clearance(d, fit = "medium") =
    let(c = fit == "fine" ? 1 : fit == "coarse" ? 3 : 2,
        r = _row(_t_clear(), d))
    abs(r[0] - d) < 1e-6 ? r[c] : d + (r[c] - r[0]);

function iso_nut_s(d) = _lk(_t_nut(), 1, d);      // Schlüsselweite
function iso_nut_m(d) = _lk(_t_nut(), 2, d);      // Mutterhöhe
// [dk, k, s, t] Zylinderkopf ISO 4762
function iso_socket_head(d) = [for (c = [1 : 4]) _lk(_t_socket(), c, d)];
// [dk, k, s] Senkkopf ISO 10642 (dk = theoretischer Ø, 90°)
function iso_csk_head(d) = [for (c = [1 : 3]) _lk(_t_csk(), c, d)];
// [dk, k, s] Linsenkopf ISO 7380
function iso_button_head(d) = [for (c = [1 : 3]) _lk(_t_button(), c, d)];
// [s, k] Sechskantkopf ISO 4017
function iso_hex_head(d) = [for (c = [1 : 2]) _lk(_t_hexhead(), c, d)];
// Senkbohrung für Zylinderkopf (DIN 974-1)
function counterbore_d(d) = _lk(_t_cbore(), 1, d);
// [Loch-Ø, Einsatzlänge] für Gewindeeinsätze M2–M8
function heatset_dims(d) = [lookup(d, _col(_t_heatset(), 1)),
                            lookup(d, _col(_t_heatset(), 2))];
// Empfohlene Mindestwand um einen Gewindeeinsatz
function heatset_wall(d) = d <= 3 ? 1.5 : d <= 4 ? 2.0 : 2.5;

// Gewinde-Kennwerte (ISO-Grundprofil)
function thread_minor_d(d, pitch) =
    let(P = is_undef(pitch) ? iso_pitch(d) : pitch) d - 1.082532 * P;
function thread_pitch_d(d, pitch) =
    let(P = is_undef(pitch) ? iso_pitch(d) : pitch) d - 0.649519 * P;
// Empfohlene Wandstärke um ein gedrucktes Innengewinde
function thread_wall(d) = max(2.4, 0.2 * d);

// ---------------------------------------------------------------------------
// 2. Gewinde
// ---------------------------------------------------------------------------
//  Aufbau: Das Gewinde ist EIN geschlossenes Polyeder (Kern + Zahn), als
//  radiales Höhenfeld r(θ, z) über n Winkelspalten. Jede Spalte enthält die
//  Profil-Knickpunkte (entlang der Schraubenlinie ausgerichtet) sowie die
//  Punkte auf z0 und z1. Benachbarte Spalten werden per Reißverschluss-
//  Verfahren nach der Profilphase u = z - P·θ/360 trianguliert. Dadurch ist
//  das Netz immer geschlossen und selbstschnittfrei; Enden sind plan.

// Knickpunkte des Zahnprofils innerhalb einer Periode (Zahnmitte bei P/2)
function _thr_bp(P, rin, rout, fc, ta) =
    let(fw = (rout - rin) * ta, c = P / 2)
    [c - fc / 2 - fw, c - fc / 2, c + fc / 2, c + fc / 2 + fw];

// Radius des Profils an der Phase u
function _thr_f(u, P, bp, rin, rout) =
    let(v = u - P * floor(u / P))
    v <= bp[0] ? rin :
    v <  bp[1] ? rin + (v - bp[0]) / (bp[1] - bp[0]) * (rout - rin) :
    v <= bp[2] ? rout :
    v <  bp[3] ? rout - (v - bp[2]) / (bp[3] - bp[2]) * (rout - rin) :
    rin;

// Binärsuche: erster Index i in [lo, hi) mit L[i] >= x (L sortiert)
function _first_ge(L, x, lo, hi) =
    lo >= hi ? lo :
    let(m = floor((lo + hi) / 2))
    L[m] >= x ? _first_ge(L, x, lo, m) : _first_ge(L, x, m + 1, hi);

// exklusive Präfixsummen
function _cum(v) = [for (i = 0, a = 0; i < len(v); a = a + v[i], i = i + 1) a];

// Spalte mit Phasenverschiebung s: Liste der u-Werte von unten nach oben
function _thr_col(P, bp, z0, z1, s, dlt) =
    let(ulo = z0 - s, uhi = z1 - s,
        g0 = 4 * floor(ulo / P), g1 = 4 * floor(uhi / P) + 3)
    concat([ulo],
           [for (g = [g0 : 1 : g1])
               let(u = floor(g / 4) * P + bp[(g % 4 + 4) % 4])
               if (u > ulo + dlt && u < uhi - dlt) u],
           [uhi]);

// Radius an einem Netzpunkt inkl. Einlauffasen.
// mode 0 = Außengewinde (Fase nach innen), 1 = Innengewinde-Schneidkörper
// (Senkung nach außen, Mündungsradius rm an den Ebenen e0 / e1).
function _thr_r(u, z, P, bp, rin, rout, mode, cb, ct, e0, e1, rm) =
    let(f = _thr_f(u, P, bp, rin, rout))
    mode == 0
      ? min(f, cb ? rin + (z - e0) : f, ct ? rin + (e1 - z) : f)
      : max(f, cb ? rm - (z - e0) : f, ct ? rm - (e1 - z) : f);

// [Punkte, Flächen] des Gewinde-Polyeders. rb > 0: koaxiale Bohrung.
function _thr_poly(P, rin, rout, fc, ta, z0, z1, n, mode, cb, ct, e0, e1, rm, rb) =
    let(bp   = _thr_bp(P, rin, rout, fc, ta),
        dlt  = P / 1000,
        U    = [for (j = [0 : n - 1]) _thr_col(P, bp, z0, z1, P * j / n, dlt)],
        lens = [for (c = U) len(c)],
        off  = _cum(lens),
        nv   = off[n - 1] + lens[n - 1],
        pts  = [for (j = [0 : n - 1])
                  let(a = 360 * j / n, s = P * j / n)
                  for (u = U[j])
                    let(z = u + s,
                        r = _thr_r(u, z, P, bp, rin, rout, mode, cb, ct, e0, e1, rm))
                    [r * cos(a), r * sin(a), z]],
        eps  = P * 1e-6,
        side = [for (j = [0 : n - 1])
                  let(A  = U[j], oa = off[j],
                      B  = j < n - 1 ? U[j + 1] : [for (u = U[0]) u - P],
                      ob = j < n - 1 ? off[j + 1] : off[0],
                      la = len(A), lb = len(B))
                  each concat(
                    [for (i = [1 : la - 1])
                        let(k = _first_ge(B, A[i] - eps, 1, lb) - 1)
                        [oa + i, ob + k, oa + i - 1]],
                    [for (k = [1 : lb - 1])
                        let(i = _first_ge(A, B[k] + eps, 1, la) - 1)
                        [ob + k, ob + k - 1, oa + i]])],
        // Deckel: Fächer zur Achse oder Ring um die Bohrung
        bore = rb > 0,
        cpts = bore
          ? concat([for (j = [0 : n - 1]) let(a = 360 * j / n) [rb * cos(a), rb * sin(a), z0]],
                   [for (j = [0 : n - 1]) let(a = 360 * j / n) [rb * cos(a), rb * sin(a), z1]])
          : [[0, 0, z0], [0, 0, z1]],
        caps = [for (j = [0 : n - 1])
                  let(jn = (j + 1) % n, oa = off[j], ob = off[jn],
                      ta_ = oa + lens[j] - 1, tb_ = ob + lens[jn] - 1)
                  each (bore
                    ? [[nv + j, oa, ob], [nv + j, ob, nv + jn],
                       [nv + n + j, tb_, ta_], [nv + n + j, nv + n + jn, tb_],
                       [nv + j, nv + jn, nv + n + jn], [nv + j, nv + n + jn, nv + n + j]]
                    : [[nv, oa, ob], [nv + 1, tb_, ta_]])])
    [concat(pts, cpts), concat(side, caps)];

// Profil-Kennwerte [P, rin, rout, fc, ta, bias]
//  Außen: Außen-Ø = d (Krone ggf. auf druckbare Breite gekürzt),
//         Kern bei d/2 - 17/24·H.
//  Innen: Schneidkörper = Außenprofil um tol (Normalabstand) vergrößert,
//         Kern bei D1/2 + tol, Mindestbreite der Mutterkrone garantiert.
//  bias:  axiale Verschiebung des Innengewindes ("seated", s. u.)
function _thr_profile(d, P, internal, t, profile, seated) =
    let(a    = profile == "printable" ? 45 : 30,     // halber Flankenwinkel
        ta   = tan(a),
        H    = P / (2 * ta),
        R    = d / 2,
        fc0  = P / 8,
        flat = max(fc0, min(0.75 * _nozzle(), P / 5)))
    !internal
      ? [P, R - 17 / 24 * H, R - (flat - fc0) / (2 * ta), flat, ta, 0]
      : let(rout = R + t,
            fc   = min(fc0 + 2 * t * (1 / cos(a) - ta), 0.6 * P),
            g    = min(flat, 0.3 * P),
            dep  = max(0.05 * P, min(rout - (R - 5 / 8 * H + t),
                                     (P - fc - g) / (2 * ta))),
            play = max(0, t / cos(a)),               // axiales Spiel je Seite
            bias = seated ? max(0, play - min(0.05, play / 2)) : 0)
        [P, rout - dep, rout, fc, ta, bias];

// Segmente pro Umdrehung: fn > $fn (begrenzt 24..72) > automatisch
function _thr_n(d, fn) =
    !is_undef(fn) ? max(8, round(fn)) :
    $fn > 0 ? min(72, max(24, round($fn))) :
    let(n = 180 / acos(1 - 0.025 / (d / 2)))
    min(72, max(24, 4 * ceil(n / 4)));

// Zentraler Gewindebaustein.
// phase: Winkel, bei dem die Zahnmitte (außen) bzw. Rillenmitte (innen)
//        die Ebene z=0 schneidet – für beide Seiten gleich definiert.
module _thread_core(d, P, length, internal, t, li, n, left, profile, phase,
                    e, rb, seated) {
    pr = _thr_profile(d, P, internal, t, profile, seated);
    rm = pr[2] + 0.5 * (pr[2] - pr[1]);          // Mündungsradius der Senkung
    z0 = internal ? -e : 0;
    z1 = internal ? length + e : length;
    data = _thr_poly(pr[0], pr[1], pr[2], pr[3], pr[4], z0, z1, n,
                     internal ? 1 : 0, li[0], li[1], 0, length, rm, rb);
    // Rohpolyeder: Zahnmitte bei z=0 unter 180°; bias hebt das Innengewinde
    // um pr[5] an (Rechtsgewinde: Drehung um -360·bias/P).
    brot = -360 * pr[5] / P;
    if (left)
        mirror([1, 0, 0]) rotate([0, 0, -phase + brot])
            polyhedron(points = data[0], faces = data[1], convexity = 10);
    else
        rotate([0, 0, phase - 180 + brot])
            polyhedron(points = data[0], faces = data[1], convexity = 10);
}

// Metrisches Gewinde (ISO 60°) als Außengewinde (positiv) oder als
// Schneidkörper für ein Innengewinde (internal=true, für difference()).
//  lead_in: true/false oder [unten, oben] – 45°-Einlauffasen
//  profile: "iso" (60°) oder "printable" (90°, 45°-Flanken, nur gedruckt
//           gegen gedruckt verwenden)
//  seated:  nur innen: Rillen um das Flankenspiel verschoben, sodass ein
//           Außengewinde gleicher phase, bis zur Schulter (z=0) eingedreht,
//           bei 0° relativer Drehung anliegt (Flächen fluchten).
module metric_thread(d = 10, pitch, length = 10, internal = false, tol,
                     lead_in = true, fn, left = false, profile = "iso",
                     phase = 0, seated = true, bore = 0, ext) {
    P = is_undef(pitch) ? iso_pitch(d) : pitch;
    assert(d > 0 && P > 0 && length > 0, "metric_thread: d, pitch, length > 0");
    li = is_list(lead_in) ? lead_in : [lead_in, lead_in];
    pr = _thr_profile(d, P, internal, _tol(tol), profile, seated);
    assert(bore == 0 || bore / 2 < pr[1] - 0.8,
           "metric_thread: bore zu groß (Wand < 0.8 mm)");
    _thread_core(d, P, length, internal, _tol(tol), li, _thr_n(d, fn), left,
                 profile, phase, _ext(ext), internal ? 0 : bore / 2, seated);
}

// Gewindestange (Außengewinde, beide Enden angefast)
module threaded_rod(d = 8, length = 30, pitch, lead_in = true, fn,
                    left = false, profile = "iso", phase = 0, bore = 0) {
    metric_thread(d, pitch, length, internal = false, lead_in = lead_in,
                  fn = fn, left = left, profile = profile, phase = phase,
                  bore = bore);
}

// Sechskant mit 30°-Fasen (Mutter-/Schraubenkopf-Form), s = Schlüsselweite
module _hex_prism(s, h, cb = true, ct = true) {
    e  = s / sqrt(3);                 // Eckenradius
    rc = 0.95 * s / 2;                // Fasenkreis an der Stirnfläche
    ro = e + 1;
    hc = (ro - rc) * tan(30);
    pts = concat([[0, 0]],
                 cb ? [[rc, 0], [ro, hc]] : [[ro, 0]],
                 ct ? [[ro, h - hc], [rc, h]] : [[ro, h]],
                 [[0, h]]);
    intersection() {
        cylinder(r = e, h = h, $fn = 6);
        rotate_extrude($fn = 48) polygon(pts);
    }
}

// Schraube: Kopf unten (z=0..k), Gewinde nach +Z (druckgerecht).
//  head: "hex" (ISO 4017) | "socket" (ISO 4762) | "countersunk" (ISO 10642)
//        | "button" (ISO 7380) | "none"
//  length: Schaftlänge unter dem Kopf; bei "countersunk" Gesamtlänge (ISO).
//  thread_length: Gewindelänge (Rest = glatter Schaft), Standard: voll.
module bolt(d = 8, length = 20, head = "hex", pitch, thread_length, fn,
            left = false, profile = "iso", tol) {
    t   = _tol(tol);
    P   = is_undef(pitch) ? iso_pitch(d) : pitch;
    hh  = head == "socket" ? iso_socket_head(d)
        : head == "countersunk" ? iso_csk_head(d)
        : head == "button" ? iso_button_head(d)
        : head == "hex" ? iso_hex_head(d) : [0, 0];
    k   = head == "none" ? 0 : hh[1];
    avail = head == "countersunk" ? length - k : length;
    tl  = is_undef(thread_length) ? avail : min(thread_length, avail);
    shank = avail - tl;
    n   = _frags(d / 2);
    assert(avail > 0, "bolt: length zu kurz für den Kopf");
    union() {
        if (head == "hex") {
            _hex_prism(hh[0], k);
        } else if (head == "socket" || head == "button") {
            difference() {
                if (head == "socket")
                    chamfer_cylinder(d = hh[0], h = k, chamfer_bottom = 0.4,
                                     chamfer_top = 0.4);
                else   // Linsenkopf: flacher Kegelstumpf mit Kuppenfase
                    rotate_extrude($fn = _frags(hh[0] / 2))
                        polygon([[0, 0], [hh[0] / 2 - 0.3, 0], [hh[0] / 2, 0.3],
                                 [hh[0] / 2, k * 0.35], [d / 2 + 0.2, k], [0, k]]);
                // Innensechskant in der Stirnfläche (z=0)
                translate([0, 0, -0.01])
                    cylinder(r = (hh[2] + t) / sqrt(3),
                             h = min(head == "socket" ? hh[3] : 0.6 * k, k - 0.8) + 0.01, $fn = 6);
            }
        } else if (head == "countersunk") {
            difference() {
                cylinder(r1 = hh[0] / 2, r2 = d / 2, h = k, $fn = _frags(hh[0] / 2));
                translate([0, 0, -0.01])
                    cylinder(r = (hh[2] + t) / sqrt(3), h = min(0.6 * k, k - 0.5) + 0.01, $fn = 6);
            }
        }
        if (shank > 0) translate([0, 0, k]) cylinder(r = d / 2, h = shank, $fn = n);
        translate([0, 0, k + shank])
            metric_thread(d, P, tl, internal = false,
                          lead_in = [head == "none" && shank == 0, true],
                          fn = fn, left = left, profile = profile);
    }
}

// Sechskantmutter ISO 4032 (Standard) mit Innengewinde, steht auf z=0.
module nut(d = 8, height, pitch, tol, s, fn, left = false, profile = "iso",
           chamfer = true) {
    h  = is_undef(height) ? iso_nut_m(d) : height;
    sw = is_undef(s) ? iso_nut_s(d) : s;
    difference() {
        _hex_prism(sw, h, chamfer, chamfer);
        metric_thread(d, pitch, h, internal = true, tol = tol, lead_in = true,
                      fn = fn, left = left, profile = profile, seated = false);
    }
}

// Gewindeloch (negativ): Innengewinde-Schneidkörper, Eintritt bei z=0.
//  through=true: Einlauffase auch am Ende (Durchgangsgewinde)
module threaded_hole(d = 8, depth = 10, pitch, tol, lead_in = true,
                     through = false, fn, left = false, profile = "iso",
                     phase = 0, seated = true, flip = false, ext) {
    _flip(flip)
        metric_thread(d, pitch, depth, internal = true, tol = tol,
                      lead_in = [lead_in, through && lead_in], fn = fn,
                      left = left, profile = profile,
                      phase = flip ? -phase : phase, seated = seated, ext = ext);
}

// Außengewinde-Zapfen zum Verbinden zweier Druckteile. Steht auf z=0
// (= Schulter/Anlagefläche), nur oben angefast.
//  bore: koaxiale Durchgangsbohrung (z. B. 8 für ein Lampenkabel)
//  flip: Zapfen zeigt nach -Z (phase bleibt in Weltwinkeln gleich)
module thread_male(d = 24, pitch, length = 15, lead_in = true, fn,
                   left = false, profile = "iso", phase = 0, bore = 0,
                   flip = false) {
    _flip(flip)
        metric_thread(d, pitch, length, internal = false,
                      lead_in = [false, lead_in], fn = fn, left = left,
                      profile = profile, phase = flip ? -phase : phase,
                      bore = bore);
}

// Passendes Innengewinde (negativ) zu thread_male mit gleichen Nennwerten.
// Tiefe = length + max(0.5, 2·tol). Mündung bei z=0 = Anlagefläche.
// Mit gleicher phase fluchten die Teile, wenn Schulter an Fläche anliegt.
module thread_female(d = 24, pitch, length = 15, tol, lead_in = true, fn,
                     left = false, profile = "iso", phase = 0, seated = true,
                     flip = false, ext) {
    t = _tol(tol);
    threaded_hole(d, length + max(0.5, 2 * t), pitch, t, lead_in, false, fn,
                  left, profile, phase, seated, flip, ext);
}

// ---------------------------------------------------------------------------
// 3. Befestigung (alles negative Formen)
// ---------------------------------------------------------------------------

// Durchgangsloch für Schrauben mit Kopfsenkung am Eintritt (z=0).
//  head: "socket" | "button" | "countersunk" | "hex" | "none"
//  head_depth: Senktiefe (Standard: Kopfhöhe + tol, Kopf bündig);
//              bei "countersunk" zusätzliche zylindrische Tiefe (Std. 0)
//  Schaft-Ø = ISO 273 (fit) + tol.  length = Klemmlänge (durchgehend).
//  sacrificial > 0: dünne Opferschicht über der Senkung (Brücke beim
//  Druck mit Senkung nach unten; nach dem Druck durchstechen).
module screw_hole(d = 3, length = 10, head = "socket", head_depth, tol,
                  fit = "medium", sacrificial = 0, flip = false, ext) {
    t  = _tol(tol);
    e  = _ext(ext);
    rs = (iso_clearance(d, fit) + t) / 2;
    ns = _frags(rs);
    sh = head == "socket" ? iso_socket_head(d)
       : head == "button" ? iso_button_head(d) : [0, 0];
    cb = head == "socket" ? max(counterbore_d(d), sh[0] + 2 * t + 0.4)
       : head == "button" ? sh[0] + 2 * t + 0.4 : 0;
    hx = iso_hex_head(d);
    hd = !is_undef(head_depth) ? head_depth
       : (head == "socket" || head == "button") ? sh[1] + t
       : head == "hex" ? hx[1] + t : 0;
    zs = (head == "socket" || head == "button" || head == "hex") && sacrificial > 0
       ? hd + sacrificial : -e;
    _flip(flip) union() {
        translate([0, 0, zs]) _hole_cyl(rs, length + e - zs, ns);
        if (head == "socket" || head == "button") {
            translate([0, 0, -e]) _hole_cyl(cb / 2, hd + e);
        } else if (head == "hex") {
            translate([0, 0, -e])
                cylinder(r = (hx[0] + 2 * t) / sqrt(3), h = hd + e, $fn = 6);
        } else if (head == "countersunk") {
            ck = iso_csk_head(d);
            rk = ck[0] / 2 + t;
            nk = _frags(rk);
            r2 = max(rs - 0.5, 0.2);
            translate([0, 0, -e]) _hole_cyl(rk, hd + e, nk);
            translate([0, 0, hd])
                cylinder(r1 = _hole_r(rk, nk), r2 = _hole_r(r2, nk), h = rk - r2, $fn = nk);
        }
    }
}

// Sechskanttasche für eine ISO-4032-Mutter (Flächen parallel zu X).
//  slot > 0: seitlicher Einschubschlitz dieser Länge in +X
//  depth: Standard Mutterhöhe + tol + 0.1
module nut_trap(d = 3, depth, tol, slot = 0, s, flip = false, ext) {
    t  = _tol(tol);
    e  = _ext(ext);
    sw = (is_undef(s) ? iso_nut_s(d) : s) + 2 * t;
    dp = is_undef(depth) ? iso_nut_m(d) + t + 0.1 : depth;
    _flip(flip) union() {
        translate([0, 0, -e]) cylinder(r = sw / sqrt(3), h = dp + e, $fn = 6);
        if (slot > 0) translate([0, -sw / 2, -e]) cube([slot, sw, dp + e]);
    }
}

// Loch für Gewinde-Einschmelzmuttern (Ruthex/CNC Kitchen), M2–M8.
//  Standard: Loch-Ø laut Tabelle, Tiefe = Einsatzlänge + 1, Fase 0.3.
module heatset_hole(d = 3, depth, hole_d, chamfer = 0.3, flip = false, ext) {
    e  = _ext(ext);
    hd = is_undef(hole_d) ? heatset_dims(d)[0] : hole_d;
    dp = is_undef(depth) ? heatset_dims(d)[1] + 1 : depth;
    n  = _frags(hd / 2);
    _flip(flip) union() {
        translate([0, 0, -e]) _hole_cyl(hd / 2, dp + e, n);
        _entry_cone(hd / 2, chamfer, e, n);
    }
}

// Magnettasche: Ø d + 2·tol, Tiefe h + depth_extra (tol hier Standard 0.1:
// 0.05 = Pressung, 0.1–0.2 = Klebesitz)
module magnet_pocket(d = 6, h = 3, tol = 0.1, depth_extra = 0.15,
                     flip = false, ext) {
    e = _ext(ext);
    _flip(flip) translate([0, 0, -e]) _hole_cyl(d / 2 + tol, h + depth_extra + e);
}

// Aufnahme für ein Lampen-Gewinderohr M10×1 (Nippel): Durchgangsloch
// Ø nipple_d + 2·tol und (optional) Sechskanttasche für die Lampenmutter
// (Schlüsselweite af, Höhe nut_h) an der Eintrittsfläche z=0.
module lamp_nipple_mount(length = 10, af = 14, nut_h = 3, nipple_d = 10,
                         tol, nut_pocket = true, flip = false, ext) {
    t = _tol(tol);
    e = _ext(ext);
    _flip(flip) union() {
        translate([0, 0, -e]) _hole_cyl(nipple_d / 2 + t, length + 2 * e);
        if (nut_pocket)
            translate([0, 0, -e])
                cylinder(r = (af + 2 * t) / sqrt(3), h = nut_h + t + 0.1 + e, $fn = 6);
    }
}

// ---------------------------------------------------------------------------
// 4. Steck- und Ausrichtverbindungen
// ---------------------------------------------------------------------------

// Konvexe 2D-Form (children) als Prisma mit 45°-Fase oben (positiv)
module _prism_chamfer_top(h, c) {
    if (c > 0)
        hull() {
            linear_extrude(height = h - c) children();
            linear_extrude(height = h) offset(delta = -c) children();
        }
    else linear_extrude(height = h) children();
}

// Konvexe 2D-Form als Tasche z=-e..dp mit 45°-Einlauffase c an z=0
module _pocket_chamfer(dp, c, e) {
    translate([0, 0, -e]) linear_extrude(height = dp + e) children();
    // Fase nur im Bereich z=0..c (hull nur über diesen kurzen Abschnitt,
    // sonst würde die Tasche über die ganze Tiefe konisch)
    if (c > 0)
        hull() {
            translate([0, 0, -e]) linear_extrude(height = e) offset(delta = c) children();
            linear_extrude(height = c) children();
        }
}

// Runder Zapfen (positiv) mit Fase oben
module peg(d = 5, h = 6, chamfer = 0.5) {
    r = d / 2;
    c = min(chamfer, r / 2, h / 2);
    n = _frags(r);
    rotate_extrude($fn = n)
        polygon(c > 0 ? [[0, 0], [r, 0], [r, h - c], [r - c, h], [0, h]]
                      : [[0, 0], [r, 0], [r, h], [0, h]]);
}

// Loch zum Zapfen: Ø d + 2·tol, Tiefe depth + tol (gleiche Werte wie peg)
module peg_hole(d = 5, depth = 6, tol, chamfer = 0.3, flip = false, ext) {
    t = _tol(tol);
    e = _ext(ext);
    r = d / 2 + t;
    n = _frags(r);
    _flip(flip) union() {
        translate([0, 0, -e]) _hole_cyl(r, depth + t + e, n);
        _entry_cone(r, chamfer, e, n);
    }
}

// D-Profil (2D): Kreis r mit Abflachung bei x = xf
module _d_shape(r, xf, n) {
    intersection() {
        circle(r = r, $fn = n);
        translate([-r - 1, -r - 1]) square([xf + r + 1, 2 * r + 2]);
    }
}

// Verdrehsicherer D-Zapfen; flat = Abflachungstiefe (Std. 15 % von d),
// Abflachung zeigt nach +X.
module d_peg(d = 6, h = 8, flat, chamfer = 0.5) {
    f = is_undef(flat) ? 0.15 * d : flat;
    n = _frags(d / 2);
    _prism_chamfer_top(h, min(chamfer, d / 4, h / 2)) _d_shape(d / 2, d / 2 - f, n);
}

module d_peg_hole(d = 6, depth = 8, flat, tol, chamfer = 0.3, flip = false, ext) {
    t = _tol(tol);
    e = _ext(ext);
    f = is_undef(flat) ? 0.15 * d : flat;
    r = d / 2 + t;
    n = _frags(r);
    _flip(flip) _pocket_chamfer(depth + t, chamfer, e) _d_shape(_hole_r(r, n), d / 2 - f + t, n);
}

// Rechteck (2D) zentriert, optional mit Eckradius r
module _rrect(sz, r) {
    if (r > 0) offset(r = r, $fn = max(8, 4 * ceil(_frags(r) / 4)))
        square([max(sz[0] - 2 * r, 0.01), max(sz[1] - 2 * r, 0.01)], center = true);
    else square(sz, center = true);
}

// Vierkant-Zapfen (Zapfen/Zapfenloch). size: Zahl oder [x, y]; r: Eckradius
module square_plug(size = 10, h = 10, chamfer = 0.5, r = 0) {
    sz = _v2(size);
    _prism_chamfer_top(h, min(chamfer, min(sz) / 4, h / 2)) _rrect(sz, r);
}

module square_socket(size = 10, depth = 10, tol, r = 0, chamfer = 0.3,
                     flip = false, ext) {
    t  = _tol(tol);
    e  = _ext(ext);
    sz = _v2(size) + [2 * t, 2 * t];
    _flip(flip) _pocket_chamfer(depth + t, chamfer, e) _rrect(sz, r > 0 ? r + t : 0);
}

// Schwalbenschwanz: Zapfen entlang Y (zentriert), Breite w am Fuß (z=0),
// nach oben um angle je Seite breiter (10–15° empfohlen).
module dovetail(w = 10, h = 5, length = 20, angle = 15) {
    dw = h * tan(angle);
    rotate([90, 0, 0]) linear_extrude(height = length, center = true)
        polygon([[-w / 2, 0], [w / 2, 0], [w / 2 + dw, h], [-w / 2 - dw, h]]);
}

// Nut dazu (negativ): Flanken um tol (Normalabstand) versetzt, Höhe h + tol,
// Länge length + 2·tol. Nut muss zum Einschieben aus dem Teil herauslaufen.
module dovetail_slot(w = 10, h = 5, length = 20, angle = 15, tol, flip = false, ext) {
    t  = _tol(tol);
    e  = _ext(ext);
    _flip(flip) rotate([90, 0, 0])
        linear_extrude(height = length + 2 * t, center = true)
            polygon([[-_dt_hw(w, angle, t, -e), -e], [_dt_hw(w, angle, t, -e), -e],
                     [_dt_hw(w, angle, t, h + t), h + t], [-_dt_hw(w, angle, t, h + t), h + t]]);
}
// halbe Nutbreite auf Höhe z
function _dt_hw(w, a, t, z) = w / 2 + z * tan(a) + t / cos(a);

// Schnapphaken-Kennwerte [Dicke, Hinterschnitt, Steg, z der Rastfläche, Kehle]
// Hinterschnitt aus zulässiger Dehnung: y = 0.67·ε·L²/t (PLA: ε ≈ 1.5–2 %)
function _snap_par(length, thickness, undercut, lead_angle, land, fillet, strain) =
    let(t  = is_undef(thickness) ? max(1.2, length / 8) : thickness,
        u  = is_undef(undercut) ? min(0.67 * strain * length * length / t, t) : undercut,
        ld = is_undef(land) ? max(0.4, _nozzle()) : land,
        zc = length - ld - u / tan(lead_angle),
        f  = is_undef(fillet) ? t / 2 : fillet)
    [t, u, ld, zc, f];

// Schnapphaken (positiv): Biegebalken x = -thickness..0, Breite in Y
// (zentriert), Höhe length ab z=0; Rastnase ragt nach +X (undercut),
// Einlaufschräge oben (lead_angle zur Z-Achse), Rastfläche waagrecht.
// Kehle (fillet) an beiden Seiten des Balkenfußes.
module snap_hook(length = 12, width = 6, thickness, undercut, lead_angle = 30,
                 land, fillet, strain = 0.02) {
    p  = _snap_par(length, thickness, undercut, lead_angle, land, fillet, strain);
    t  = p[0]; u = p[1]; ld = p[2]; zc = p[3]; f = p[4];
    assert(zc > f + 0.5, "snap_hook: Haken zu kurz für Nase und Kehle");
    rotate([90, 0, 0]) linear_extrude(height = width, center = true)
        polygon(concat(f > 0 ? [[-t - f, 0], [f, 0], [0, f]] : [[-t, 0], [0, 0]],
                       [[0, zc], [u, zc], [u, zc + ld], [0, length], [-t, length]],
                       f > 0 ? [[-t, f]] : []));
}

// Rastfenster (negativ) zum snap_hook, im Koordinatensystem des Hakens:
// gleiche Parameter übergeben. Die Gegenwand liegt bei x >= 0 (Innenfläche
// an der Balkenvorderseite) mit Dicke wall und muss oberhalb der Kehle
// (z > fillet) beginnen. Fenster: Breite + 2·tol, von zc - tol bis
// length + tol – die Rastfläche hat also tol axiales Spiel.
module snap_window(length = 12, width = 6, thickness, undercut, lead_angle = 30,
                   land, fillet, strain = 0.02, tol, wall = 3, ext) {
    p  = _snap_par(length, thickness, undercut, lead_angle, land, fillet, strain);
    tl = _tol(tol);
    e  = _ext(ext);
    translate([-e, -(width / 2 + tl), p[3] - tl])
        cube([max(wall, p[1] + tl) + 2 * e, width + 2 * tl, length - p[3] + 2 * tl]);
}

// Ringsektor r0..r1, z0..z1, Winkel a0..a0+a
module _sector(r0, r1, z0, z1, a0, a, n) {
    translate([0, 0, z0]) rotate([0, 0, a0])
        rotate_extrude(angle = a, $fn = n)
            translate([r0, 0]) square([r1 - r0, z1 - z0]);
}

// Bajonett-Stecker (positiv): Zylinder Ø d, Höhe h, mit `lugs` Nocken
// (Bogenbreite lug_w, Höhe lug_h, radial lug_depth), Nockenmitte bei
// lug_z (Standard: 1 mm unter der Oberkante). Nocken 0 liegt bei +X.
module bayonet_male(d = 30, h = 10, lugs = 3, lug_w = 6, lug_h = 3,
                    lug_depth = 1.5, lug_z) {
    r  = d / 2;
    zl = is_undef(lug_z) ? h - lug_h / 2 - 1 : lug_z;
    a  = lug_w / r * 180 / PI;
    n  = _frags(r + lug_depth);
    union() {
        cylinder(r = r, h = h, $fn = _frags(r));
        for (i = [0 : lugs - 1])
            _sector(r - 0.5, r + lug_depth, zl - lug_h / 2, zl + lug_h / 2,
                    i * 360 / lugs - a / 2, a, n);
    }
}

// Bajonett-Aufnahme (negativ), gleiche Parameter wie bayonet_male.
// Einführen bei 0°, dann den Stecker um +twist (gegen den Uhrzeigersinn,
// von oben) drehen – bzw. das Buchsenteil im Uhrzeigersinn. Bohrung
// Ø d + 2·tol, Tiefe h + tol; alle Schlitze mit tol Spiel.
module bayonet_socket(d = 30, h = 10, lugs = 3, lug_w = 6, lug_h = 3,
                      lug_depth = 1.5, lug_z, twist = 30, tol, flip = false, ext) {
    t  = _tol(tol);
    e  = _ext(ext);
    r  = d / 2;
    zl = is_undef(lug_z) ? h - lug_h / 2 - 1 : lug_z;
    a  = lug_w / r * 180 / PI;
    da = t / r * 180 / PI;                 // Winkelspiel (am Radius r = tol)
    n  = _frags(r + lug_depth + t);
    ro = _hole_r(r + lug_depth + t, n);
    _flip(flip) union() {
        translate([0, 0, -e]) _hole_cyl(r + t, h + t + e);
        for (i = [0 : lugs - 1]) {
            c = i * 360 / lugs;
            _sector(r - 0.5, ro, -e, zl + lug_h / 2 + t, c - a / 2 - da, a + 2 * da, n);
            _sector(r - 0.5, ro, zl - lug_h / 2 - t, zl + lug_h / 2 + t,
                    c - a / 2 - da, a + 2 * da + twist, n);
        }
    }
}

// ---------------------------------------------------------------------------
// 5. Hilfsformen
// ---------------------------------------------------------------------------

// Ring eines abgerundeten Quaders: 4 Viertelkreise (Radius rho) um die
// inneren Ecken (±hx, ±hy) auf Höhe z, gegen den Uhrzeigersinn (von oben)
function _rb_ring(hx, hy, rho, z, k) =
    [for (c = [0 : 3])
        let(cx = (c == 0 || c == 3) ? hx : -hx, cy = c < 2 ? hy : -hy)
        for (i = [0 : k]) let(a = 90 * c + 90 * i / k)
            [cx + rho * cos(a), cy + rho * sin(a), z]];

// Konvexes Polyeder eines abgerundeten Quaders (XY zentriert, z = 0..sz),
// direkt erzeugt statt hull() über Kugeln (in 2021.01 sehr langsam).
// top_only: Unterseite flach (nur senkrechte + obere Kanten gerundet).
module _rbox_poly(s, r, top_only, n) {
    k  = max(1, round(n / 4));
    m  = max(1, round(n / 4));
    hx = s[0] / 2 - r;
    hy = s[1] / 2 - r;
    N  = 4 * (k + 1);
    rings = concat(
        top_only ? [_rb_ring(hx, hy, r, 0, k)]
                 : [for (i = [1 : m]) let(p = -90 + 90 * i / m)
                        _rb_ring(hx, hy, r * cos(p), r + r * sin(p), k)],
        [for (i = [0 : m - 1]) let(p = 90 * i / m)
            _rb_ring(hx, hy, r * cos(p), s[2] - r + r * sin(p), k)]);
    nr = len(rings);
    it = nr * N;                                  // oberer Pol (4 Punkte)
    ib = nr * N + 4;                              // unterer Pol (4 Punkte)
    last = (nr - 1) * N;
    pts = concat([for (R = rings) each R],
                 [[hx, hy, s[2]], [-hx, hy, s[2]], [-hx, -hy, s[2]], [hx, -hy, s[2]]],
                 top_only ? [] : [[hx, hy, 0], [-hx, hy, 0], [-hx, -hy, 0], [hx, -hy, 0]]);
    side = [for (j = [0 : nr - 2], i = [0 : N - 1])
              let(a0 = j * N + i, a1 = j * N + (i + 1) % N, b0 = a0 + N, b1 = a1 + N)
              each [[a0, b0, b1], [a0, b1, a1]]];
    topf = concat(
        [for (c = [0 : 3], i = [0 : k - 1]) let(a = last + c * (k + 1) + i) [a, it + c, a + 1]],
        [for (c = [0 : 3])
            let(a = last + c * (k + 1) + k, a1 = last + ((c + 1) % 4) * (k + 1))
            each [[a, it + c, it + (c + 1) % 4], [a, it + (c + 1) % 4, a1]]],
        [[it + 3, it + 2, it + 1, it]]);
    botf = top_only ? [[for (i = [0 : N - 1]) i]] : concat(
        [for (c = [0 : 3], i = [0 : k - 1]) let(b = c * (k + 1) + i) [ib + c, b, b + 1]],
        [for (c = [0 : 3])
            let(b = c * (k + 1) + k, b1 = ((c + 1) % 4) * (k + 1))
            each [[ib + c, b, b1], [ib + c, b1, ib + (c + 1) % 4]]],
        [[ib, ib + 1, ib + 2, ib + 3]]);
    polyhedron(points = pts, faces = concat(side, topf, botf), convexity = 2);
}

// Quader mit gerundeten Kanten (Semantik wie cube: center=false → Ecke im
// Ursprung). edges: "z" = nur senkrechte Kanten (druckt am besten),
// "top" = senkrechte + obere Kanten (Unterseite bleibt flach), "all".
module rounded_box(size = [20, 20, 10], r = 2, center = false, edges = "z") {
    s  = is_list(size) ? size : [size, size, size];
    rr = min(r, min(s[0], s[1]) / 2 - 0.01, edges == "z" ? 1e9 : s[2] / 2 - 0.01);
    n  = max(8, 4 * ceil(_frags(max(rr, 0.1)) / 4));
    translate(center ? -s / 2 : [0, 0, 0]) {
        if (rr <= 0) cube(s);
        else if (edges == "z")
            hull() for (x = [rr, s[0] - rr], y = [rr, s[1] - rr])
                translate([x, y, 0]) cylinder(r = rr, h = s[2], $fn = n);
        else
            translate([s[0] / 2, s[1] / 2, 0]) _rbox_poly(s, rr, edges == "top", n);
    }
}

// Zylinder mit 45°-Fasen unten/oben (unten gegen Elefantenfuß: 0.4–0.6)
module chamfer_cylinder(d = 10, h = 10, chamfer_bottom = 0, chamfer_top = 0,
                        center = false) {
    r  = d / 2;
    cb = max(0, min(chamfer_bottom, r - 0.01, 0.49 * h));
    ct = max(0, min(chamfer_top, r - 0.01, 0.49 * h));
    translate([0, 0, center ? -h / 2 : 0])
        rotate_extrude($fn = _frags(r))
            polygon(concat([[0, 0]], cb > 0 ? [[r - cb, 0], [r, cb]] : [[r, 0]],
                           ct > 0 ? [[r, h - ct], [r - ct, h]] : [[r, h]], [[0, h]]));
}

// Tropfenprofil (2D), Spitze nach +Y, umschreibt den Kreis r
function _teardrop_pts(r, n, truncate) =
    let(rc  = _hole_r(r, n),
        k   = ceil(270 / (360 / n)),
        arc = [for (i = [0 : k]) let(a = 45 - i * 270 / k) [rc * cos(a), rc * sin(a)]],
        tip = rc * sqrt(2))
    truncate ? concat(arc, [[-(tip - rc), rc], [tip - rc, rc]])
             : concat(arc, [[0, tip]]);

// Waagrechtes Loch ohne Stützmaterial (negativ): Achse entlang +X ab x=0
// (center=true: mittig), Spitze nach +Z. Ø = d + 2·tol; ragt an beiden
// Enden um ext über. truncate=true: Spitze auf Höhe r abgeflacht.
module teardrop_hole(d = 5, length = 10, tol, center = false, truncate = false, ext) {
    t = _tol(tol);
    e = _ext(ext);
    r = d / 2 + t;
    translate([center ? -length / 2 - e : -e, 0, 0])
        rotate([90, 0, 90]) linear_extrude(height = length + 2 * e)
            polygon(_teardrop_pts(r, _frags(r), truncate));
}

// Kabelkanal (negativ) entlang +X: Kanal-Ø = d (Kabel) + clearance
// (H03VV-F 2×0.75 ≈ 6 mm → Kanal ≥ 8 mm). teardrop=true für waagrechten Druck.
module cable_channel(d = 6, length = 20, clearance = 2, teardrop = true,
                     center = false, truncate = false, ext) {
    e  = _ext(ext);
    dd = d + clearance;
    if (teardrop)
        teardrop_hole(dd, length, tol = 0, center = center, truncate = truncate, ext = e);
    else
        translate([center ? -length / 2 - e : -e, 0, 0]) rotate([0, 90, 0])
            _hole_cyl(dd / 2, length + 2 * e);
}

// Kabelweg (negativ) als Kette von Kugeln durch die Punkte `points`.
// Biegeradius für Netzkabel ≥ 6×Kabel-Ø: Ecken über mehrere Punkte runden.
module cable_path(points, d = 6, clearance = 2) {
    dd = d + clearance;
    n  = min(24, _frags(dd / 2));      // grob genug für schnelles hull() in 2021.01
    rr = _hole_r(_hole_r(dd / 2, n), n);
    for (i = [0 : len(points) - 2])
        hull() {
            translate(points[i]) sphere(r = rr, $fn = n);
            translate(points[i + 1]) sphere(r = rr, $fn = n);
        }
}

// linear_extrude einer 2D-Form (children) mit gestufter Fase unten gegen
// den Elefantenfuß (erste `chamfer` mm um bis zu `chamfer` eingezogen).
module elephant_foot_extrude(height = 10, chamfer = 0.4, steps = 2) {
    c = max(0, min(chamfer, height / 2));
    union() {
        translate([0, 0, c]) linear_extrude(height = height - c) children();
        if (c > 0)
            for (i = [0 : steps - 1])
                translate([0, 0, i * c / steps]) linear_extrude(height = c / steps)
                    offset(delta = -c * (steps - i) / steps) children();
    }
}

// Passt ein Teil (size = [x, y, z] oder [x, y]) auf das Druckbett?
// XY darf um 90° gedreht werden. Standard: Anycubic Kobra 2 Neo.
function fits_bed(size, bed = [220, 220, 250], margin = 0) =
    let(s = len(size) == 2 ? [size[0], size[1], 0] : size, m = 2 * margin)
    ((s[0] + m <= bed[0] && s[1] + m <= bed[1]) ||
     (s[1] + m <= bed[0] && s[0] + m <= bed[1])) && s[2] <= bed[2];

// Bauraum-Rahmen nur zur Vorschau (%-Modifikator: nie im STL).
module bed_outline(bed = [220, 220, 250], center = true) {
    module frame() difference() {
        square([bed[0], bed[1]]);
        translate([1, 1]) square([bed[0] - 2, bed[1] - 2]);
    }
    % translate(center ? [-bed[0] / 2, -bed[1] / 2, 0] : [0, 0, 0]) {
        translate([0, 0, -0.2]) linear_extrude(height = 0.2) frame();
        translate([0, 0, bed[2] - 0.2]) linear_extrude(height = 0.2) frame();
        for (x = [0, bed[0] - 1], y = [0, bed[1] - 1])
            translate([x, y, 0]) cube([1, 1, bed[2]]);
    }
}
