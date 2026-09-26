/* ===========================================================================
   Modulare Lampe (Mori Concept) – Tisch- oder Stehlampe
   „Ein System. Viele Möglichkeiten.“ Verdrehter Schirm mit Texturen, Magnet-Schirmhalter,
   verschraubte Säulenmodule (M24×3), Dreibein mit Steckverbindung. Nach den Konzeptbildern
   mit SCAD Studio erzeugt; alle Teile passen auf den Anycubic Kobra 2 Neo.

   Aufbau (von oben nach unten):
     Schirm (verdreht, Texturen)      – per 4 Magneten auf dem Schirmhalter, werkzeuglos tauschbar
     Schirmhalter                     – sitzt auf dem Außengewinde der E27-Fassung (Schirmring)
     Modulwürfel                      – Aufnahme der Fassung (M10×1-Gewinderohr + Mutterntasche)
     Drehmodul(e)                     – leicht verdreht, M24×3-Gewinde, fluchten festgeschraubt
     Körper (Oberteil)                – M24×3-Gewinde in die Fußaufnahme
     Fußaufnahme                      – 3 Vierkant-Steckverbindungen (Spaltmaß 0,2 mm) + M4-Sicherungsschraube
     Beine (Tischlampe 1-teilig, Stehlampe 2-teilig mit Verbinder) + TPU-Fußkappen
   Durchgehender Kabelkanal Ø 8 mm von der Fußaufnahme bis zur Fassung.

   Druckteile (alle passen auf den Anycubic Kobra 2 Neo, 220 × 220 × 250 mm):
     shade          Schirm           1×  stehend, 0,4 mm Düse, 2–3 Wände, weißes PLA, keine Stützen
     shade_holder   Schirmhalter     1×  flach
     socket_cube    Modulwürfel      1×  Oberseite nach unten (liegt so bereits im Export)
     twist_module   Drehmodul        1× Tisch / 2× Steh   Oberseite nach unten
     body           Körper           1×  Oberseite nach unten
     hub            Fußaufnahme      1×  aufrecht, Überhänge ≤ 45°
     leg            Bein             3×  (Tischlampe) liegend
     leg_upper/leg_lower/leg_coupler 3× je (Stehlampe) liegend
     foot_pad       Fußkappe         3×  TPU, aufkleben
   Empfohlen: 0,2 mm Schichthöhe, 3 Wände, 20–30 % Gyroid; Gewinde-Teile senkrecht (wie exportiert).

   Zukaufteile:
     1× E27-Fassung mit Außengewinde + 2 Schirmringe, 1× Gewinderohr M10×1 (20–25 mm) + Mutter SW 14
     1× Anschlussleitung 2×0,75 mm² (rund, Ø ≤ 6,3 mm) mit Schalter und Stecker, Zugentlastung
     8× Neodym-Magnet 10 × 3 mm (Polung beachten!), 3× Gewindeeinsatz M4 (Ø 5,6 mm) + 3× M4×12 Zylinderkopf
     Stehlampe zusätzlich: 6× Gewindeeinsatz M3 + 6× M3×10 Senkkopf
   ⚠ Netzspannung: nur CE-geprüfte Fassung/Leitung verwenden; im Zweifel eine Elektrofachkraft
     anschließen lassen. Nur LED-Leuchtmittel ≤ 10 W (PLA wird ab ca. 55 °C weich).

   Montage:
     1. Einsätze (M4 in die Beine, M3 in die Verbinder) mit dem Lötkolben einschmelzen, Magnete einkleben.
     2. Beine in die Fußaufnahme stecken, je eine M4-Schraube von außen festziehen.
     3. Kabel von unten durch Fußaufnahme, Körper, Drehmodul(e) und Würfel fädeln.
     4. Körper, Module und Würfel bis zum Anschlag einschrauben – die Flächen fluchten dann.
     5. Gewinderohr mit Mutter im Würfel einsetzen, Fassung aufschrauben und anschließen.
     6. Unteren Schirmring, Schirmhalter, oberen Schirmring montieren, Schirm aufsetzen.
   In OpenSCAD: Fenster → Customizer für alle Parameter. `part` wählt das zu exportierende Teil.
   =========================================================================== */

use <studio.scad>;

/* [Ansicht] */
part = "assembly"; // [assembly:Zusammenbau, all_parts:Alle Teile (Druckansicht), shade:Schirm, shade_holder:Schirmhalter, socket_cube:Modulwürfel, twist_module:Drehmodul, body:Körper (Oberteil), hub:Fußaufnahme, leg:Bein (Tischlampe), leg_upper:Bein oben (Stehlampe), leg_lower:Bein unten (Stehlampe), leg_coupler:Beinverbinder (Stehlampe), foot_pad:Fußkappe (TPU), hw_socket:Normteil E27-Fassung, hw_screws:Normteil Schrauben, hw_magnets:Normteil Magnete, hw_cable:Normteil Kabel, hw_bulb_clearance:Leuchtmittel + Abstand]
explode = 0; // [0:5:150] Explosionsansicht in mm (nur Zusammenbau)

/* [Variante] */
variant = "table"; // [table:Tischlampe, floor:Stehlampe]

/* [Schirm] */
shade_w = 120;          // [80:5:170] Kantenlänge
shade_h = 120;          // [80:5:200] Höhe
shade_twist = 12;       // [0:1:45] Verdrehung über die Höhe (°)
shade_wall = 1.2;       // [0.8:0.4:2.4] Wandstärke (1,2 = schön lichtdurchlässig)
shade_texture = "streifen"; // [glatt:Glatt, streifen:Vertikale Streifen, wellen:Wellen, rippen:Grobe Rippen]

/* [Säule] */
col_w = 44;             // [36:1:60] Säulenbreite
col_chamfer = 5;        // [0:0.5:10] Kantenfase der Säule
body_h = 60;            // [30:5:120] Höhe Körper (Oberteil)
body_twist = 15;        // [0:1:45]
mod_h = 50;             // [30:5:100] Höhe Drehmodul
mod_twist = 20;         // [0:1:45]
cube_h = 40;            // [30:1:60] Höhe Modulwürfel
table_modules = 1;      // [0:1:4] Drehmodule Tischlampe (Säule 150 mm)
floor_modules = 2;      // [0:1:6] Drehmodule Stehlampe (Säule 200 mm)

/* [Dreibein] */
leg_angle = 45;         // [30:1:55] Spreizung gegen die Senkrechte (≥ 45° = Fußaufnahme stützenfrei)
leg_w = 24;             // [16:1:30] Bein-Querschnitt
table_leg = 170;        // [120:5:185] Beinlänge Tischlampe (ab Schulter)
floor_leg = 250;        // [180:5:390] Beinlänge Stehlampe (2-teilig)
pad_t = 1.2;            // [0.8:0.2:3] Stärke TPU-Fußkappe

/* [Verbindungen] */
thread_d = 24;          // [16:2:36] Säulengewinde M…
thread_p = 3;           // [1.5:0.5:4] Steigung
thread_len = 12;        // [8:1:20] Gewindelänge (= 4 Gänge)
thread_profile = "printable"; // [printable:Druckoptimiert (45°-Flanken, stützenfrei), iso:ISO 60°]
cable_d = 8;            // [6:0.5:12] Kabelkanal Ø (Kabel ≤ 6,3 mm)
tenon = 16;             // [10:1:22] Vierkant-Zapfen der Beine
tenon_len = 22;         // [12:1:30] Zapfenlänge
lock_screw = 4;         // [3:1:5] Sicherungsschraube M… (mit Gewindeeinsatz)
magnet_d = 10;          // [6:1:15] Magnet Ø
magnet_h = 3;           // [2:1:5] Magnet Höhe
socket_thread_d = 40;   // [27:0.5:45] Außengewinde der Fassung (E27 ≈ 40, E14 ≈ 28) – nachmessen!
socket_h = 40;          // [30:1:70] Höhe der Fassung über dem Würfel
holder_z = 18;          // [5:1:40] Höhe des Schirmhalters über dem Würfel
bulb_d = 45;            // [40:5:80] Leuchtmittel Ø (G45-Tropfen; A60 = 60)
bulb_h = 80;            // [60:5:140] Leuchtmittel Höhe (A60 ≈ 105 → Schirm ≥ 130)
bulb_gap = 25;          // [20:1:40] Mindestabstand Leuchtmittel ↔ Schirm

/* [Drucker] */
bed = [220, 220, 250];  // Bauraum X, Y, Z in mm (Anycubic Kobra 2 Neo)
nozzle = 0.4;           // [0.2:0.1:1.0]
tol = 0.2;              // [0.05:0.05:0.6] Spaltmaß pro Seite

/* [Qualität] */
$fa = 4;
$fs = 0.5;

/* [Hidden] */
$tol = tol;
$nozzle = nozzle;
eps = 0.01;
is_floor = variant == "floor";
n_mod = is_floor ? floor_modules : table_modules;
leg_len = is_floor ? floor_leg : table_leg;
leg_split = ceil(leg_len / 2);                  // Teilung der Stehlampen-Beine
holder_t = 5;                                   // Schirmhalter-Stärke
tab_t = 5;                                      // Magnet-Laschen im Schirm
shade_inner = shade_w / 2 - shade_wall;         // innerer Halbmesser des Schirms
mag_r = shade_inner - 10;                       // Radius der Magnetpaare
coupler = 14;                                   // Querschnitt Beinverbinder
coupler_len = 40;

// Fußaufnahme: Schulterpunkt der Beinachse (radial, Höhe) und Maße
hub_rs = 34;
hub_zs = 8;
hub_h = 44;
hub_r = 30;
boss_side = 5;          // Wand seitlich des Zapfens
boss_over = 9;          // Wand über dem Zapfen (für den Schraubenkopf)
boss_back = 5;          // Wand hinter dem Zapfenende

// Höhen im Zusammenbau
foot_z = hub_zs - leg_len * cos(leg_angle);     // Fußebene im Nabensystem
hub_z = pad_t - foot_z;                          // Unterkante Fußaufnahme über dem Boden
col_z = hub_z + hub_h;                           // Unterkante Körper
cube_z = col_z + body_h + n_mod * mod_h;         // Unterkante Würfel
cube_top = cube_z + cube_h;
shade_z = cube_top + holder_z + holder_t;        // Unterkante Schirm
r_cube = -(body_twist + n_mod * mod_twist);      // Drehlage des Würfels (Flächen fluchten)
function r_mod(k) = -(body_twist + k * mod_twist);

assert(shade_inner - (bulb_d / 2 + bulb_gap) > 0, "Leuchtmittel zu nah am Schirm – bulb_gap oder shade_w anpassen");
assert(socket_h + bulb_h <= holder_z + holder_t + shade_h, "Leuchtmittel ragt oben aus dem Schirm – shade_h erhöhen");
assert(shade_wall >= 2 * nozzle, "Schirmwand dünner als 2 Linienbreiten");
assert(col_w / 2 - col_chamfer > thread_d / 2 + 2.5, "Säule zu schmal für das Gewinde");

echo(str("Gesamthöhe ca. ", shade_z + shade_h, " mm, Säule ", body_h + n_mod * mod_h + cube_h,
         " mm, Fußkreis Ø ", 2 * (hub_rs + leg_len * sin(leg_angle)) + leg_w, " mm"));

// ===========================================================================
// 2D-Profile
// ===========================================================================

// Säulenprofil: Quadrat mit angefasten Ecken (Achteck)
module col_profile() {
    a = col_w / 2;
    c = col_chamfer;
    polygon([[a - c, -a], [a, -a + c], [a, a - c], [a - c, a],
             [-a + c, a], [-a, a - c], [-a, -a + c], [-a + c, -a]]);
}

// Schirm-Außenkontur je nach Textur
module shade_outline() {
    a = shade_w / 2;
    if (shade_texture == "wellen") {
        n = 6;           // Wellen je Seite
        amp = 1.4;
        pts = [for (s = [0 : 3], i = [0 : 47])
                   let(t = i / 48, x = -a + 2 * a * t,
                       o = amp * (1 - cos(360 * n * t)) / 2)
                   _rot2([x, -a - o], 90 * s)];
        polygon(pts);
    } else if (shade_texture == "streifen" || shade_texture == "rippen") {
        n = shade_texture == "streifen" ? 11 : 5;
        rw = shade_texture == "streifen" ? 2.4 : 6;
        rd = shade_texture == "streifen" ? 1.0 : 1.8;
        offset(r = 0.4) offset(delta = -0.4) union() {
            offset(r = 2) square(shade_w - 4, center = true);
            for (s = [0 : 3], i = [0 : n - 1])
                rotate(90 * s)
                    translate([-a + (i + 0.5) * shade_w / n - rw / 2, -a - rd + eps])
                        square([rw, rd + 0.5]);
        }
    } else {
        offset(r = 2) square(shade_w - 4, center = true);
    }
}
function _rot2(p, ang) = [p[0] * cos(ang) - p[1] * sin(ang), p[0] * sin(ang) + p[1] * cos(ang)];

// ===========================================================================
// Säulensegment (Zusammenbau-Lage: Unterseite z=0, Profil unten unverdreht)
//   male:   M24-Zapfen nach unten (Phase 0 im eigenen System)
//   female: M24-Innengewinde oben, um -twist gedreht → nächstes Segment fluchtet
// ===========================================================================
module column_segment(h, twist, male = true, female = true) {
    difference() {
        union() {
            linear_extrude(height = h, twist = twist, slices = max(2, ceil(abs(twist) / 2)),
                           convexity = 4)
                col_profile();
            if (male)
                thread_male(d = thread_d, pitch = thread_p, length = thread_len,
                            bore = cable_d, flip = true, phase = 0, profile = thread_profile);
        }
        if (female)
            translate([0, 0, h])
                thread_female(d = thread_d, pitch = thread_p, length = thread_len,
                              flip = true, phase = -twist, profile = thread_profile);
        translate([0, 0, -thread_len - 1]) cylinder(d = cable_d, h = h + thread_len + 2);
    }
}

// Körper, Drehmodul, Würfel in Zusammenbau-Lage
module body_asm() column_segment(body_h, body_twist);
module module_asm() column_segment(mod_h, mod_twist);

module cube_asm() {
    difference() {
        column_segment(cube_h, 0, male = true, female = false);
        // Gewinderohr M10×1 mit Mutterntasche (SW 14) von oben – die Fassung klemmt die Mutter
        translate([0, 0, cube_h]) lamp_nipple_mount(length = 16, af = 14, nut_h = 3, flip = true);
        // Kantenfase oben (Optik, Elefantenfuß beim Druck kopfüber)
        translate([0, 0, cube_h - 0.6])
            linear_extrude(height = 1, convexity = 4)
                difference() { square(col_w + 10, center = true); offset(delta = -0.6) col_profile(); }
    }
}

// Druckausrichtung: kopfüber, Oberseite auf dem Bett
module socket_cube()   rotate([180, 0, 0]) translate([0, 0, -cube_h]) cube_asm();
module twist_module()  rotate([180, 0, 0]) translate([0, 0, -mod_h]) module_asm();
module body()          rotate([180, 0, 0]) translate([0, 0, -body_h]) body_asm();

// ===========================================================================
// Schirm + Schirmhalter
// ===========================================================================
module shade() {
    union() {
        linear_extrude(height = shade_h, twist = shade_twist, slices = ceil(shade_h / 4),
                       convexity = 6)
            difference() {
                shade_outline();
                square(2 * shade_inner, center = true);
            }
        // 4 Magnet-Laschen unten innen (auf dem Bett gedruckt, Tasche nach unten offen)
        for (s = [0 : 3]) rotate(90 * s)
            difference() {
                hull() {
                    translate([mag_r, 0, 0]) cylinder(d = magnet_d + 6, h = tab_t);
                    translate([shade_inner - 1, -(magnet_d + 6) / 2, 0])
                        cube([1 + 0.5, magnet_d + 6, tab_t]);
                }
                translate([mag_r, 0, 0]) magnet_pocket(d = magnet_d, h = magnet_h);
            }
    }
}

module shade_holder() {
    ring_id = socket_thread_d + 2 * 0.4;
    difference() {
        union() {
            cylinder(d = ring_id + 12, h = holder_t);
            for (s = [0 : 3]) rotate(90 * s) {
                translate([0, -5, 0]) cube([mag_r, 10, holder_t]);
                translate([mag_r, 0, 0]) cylinder(d = magnet_d + 6, h = holder_t);
            }
        }
        translate([0, 0, -1]) cylinder(d = ring_id, h = holder_t + 2);
        for (s = [0 : 3]) rotate(90 * s)
            translate([mag_r, 0, holder_t]) magnet_pocket(d = magnet_d, h = magnet_h, flip = true);
    }
}

// ===========================================================================
// Fußaufnahme + Beine
// ===========================================================================
function leg_theta(k) = 90 + 120 * k;
function dvec(th) = [sin(leg_angle) * cos(th), sin(leg_angle) * sin(th), -cos(leg_angle)];
function uvec(th) = [cos(leg_angle) * cos(th), cos(leg_angle) * sin(th), sin(leg_angle)];
function cross3(p, q) = [p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0]];

// Bein-Koordinaten (= Druckausrichtung des Beins: x entlang der Achse zum Fuß,
// z = 0 ist die im Zusammenbau obere Fläche) → Koordinaten der Fußaufnahme
module leg_frame(k) {
    th = leg_theta(k);
    d = dvec(th);
    u = uvec(th);
    y = cross3(d, u);
    o = [hub_rs * cos(th), hub_rs * sin(th), hub_zs] + (leg_w / 2) * u;
    multmatrix([[d[0], y[0], -u[0], o[0]],
                [d[1], y[1], -u[1], o[1]],
                [d[2], y[2], -u[2], o[2]],
                [0, 0, 0, 1]])
        children();
}

// Zapfen / Buchse: bündig mit der oberen Fläche (z=0), Achse nach -x
module tenon_shape()  translate([0, 0, tenon / 2]) rotate([0, -90, 0]) square_plug(size = tenon, h = tenon_len);
module tenon_socket() translate([0, 0, tenon / 2]) rotate([0, -90, 0]) square_socket(size = tenon, depth = tenon_len);
lock_x = -tenon_len / 2;        // Lage der Sicherungsschraube

module hub() {
    difference() {
        intersection() {
            hull() {
                cylinder(r = hub_r, h = hub_h);
                for (k = [0 : 2]) leg_frame(k)
                    translate([-(tenon_len + boss_back), -(tenon / 2 + boss_side), -boss_over])
                        cube([tenon_len + boss_back, tenon + 2 * boss_side, boss_over + tenon + boss_side]);
            }
            // flache Unterseite
            translate([-200, -200, 0]) cube([400, 400, hub_h]);
        }
        for (k = [0 : 2]) leg_frame(k) {
            // Sitzfläche senkrecht zur Beinachse
            translate([0, -100, -100]) cube([200, 200, 200]);
            tenon_socket();
            // Sicherungsschraube M4 von außen, Kopf versenkt
            translate([lock_x, 0, -boss_over - 10])
                screw_hole(d = lock_screw, length = boss_over + 10 + 2, head = "socket",
                           head_depth = 10 + iso_socket_head(lock_screw)[1] + 0.2);
        }
        // Gewinde für den Körper + Kabelkanal
        translate([0, 0, hub_h]) thread_female(d = thread_d, pitch = thread_p, length = thread_len,
                                               flip = true, phase = 0, profile = thread_profile);
        translate([0, 0, -1]) cylinder(d = cable_d, h = hub_h + 2);
        // Kabelauslass unten angefast
        translate([0, 0, -eps]) cylinder(d1 = cable_d + 4, d2 = cable_d, h = 2);
    }
}

// Beinkörper von x0 bis x1 (Druckausrichtung), Fußschnitt optional
module leg_bar(x0, x1, foot) {
    difference() {
        translate([x0, 0, leg_w / 2])
            rotate([0, 90, 0])   // Querschnitt mit Kantenfasen, Achse entlang x
                linear_extrude(height = x1 - x0)
                    offset(delta = 1.2, chamfer = true) offset(delta = -1.2) square(leg_w, center = true);
        if (foot) foot_cut();
    }
}

// waagerechter Fußschnitt: alles unterhalb der Bodenebene entfernen
module foot_cut() {
    translate([leg_len, 0, leg_w / 2]) rotate([0, -(90 + leg_angle), 0])
        translate([-300, -300, -600]) cube([600, 600, 600]);
}

foot_extra = leg_w * tan(leg_angle) / 2 + 2;   // Überstand bis zum Fußschnitt

module leg() {
    if (!is_floor)
        difference() {
            union() {
                leg_bar(0, leg_len + foot_extra, true);
                tenon_shape();
            }
            // Gewindeeinsatz M4 von der oberen Fläche (liegt beim Druck auf dem Bett)
            translate([lock_x, 0, 0]) heatset_hole(d = lock_screw);
        }
}

// Stehlampe: zweiteilige Beine mit Vierkant-Verbinder und M3-Senkschrauben von unten
module coupler_socket() translate([leg_split, 0, leg_w / 2]) cube([coupler_len + 2 * tol, coupler + 2 * tol, coupler + 2 * tol], center = true);
module coupler_screws() for (s = [-1, 1])
    translate([leg_split + s * coupler_len / 4, 0, leg_w]) screw_hole(d = 3, length = leg_w / 2, head = "countersunk", flip = true);

module leg_upper() {
    if (is_floor)
        difference() {
            union() { leg_bar(0, leg_split, false); tenon_shape(); }
            translate([lock_x, 0, 0]) heatset_hole(d = lock_screw);
            coupler_socket();
            coupler_screws();
        }
}

module leg_lower() {
    if (is_floor)
        translate([-leg_split, 0, 0])   // Druckausrichtung: beginnt bei x=0
            difference() {
                leg_bar(leg_split, leg_len + foot_extra, true);
                coupler_socket();
                coupler_screws();
            }
}

// Verbinder in Zusammenbau-Lage (Beinkoordinaten), Einsätze zeigen zur Beinunterseite
module leg_coupler_asm() {
    difference() {
        translate([leg_split, 0, leg_w / 2]) cube([coupler_len, coupler, coupler], center = true);
        for (s = [-1, 1])
            translate([leg_split + s * coupler_len / 4, 0, leg_w / 2 + coupler / 2])
                heatset_hole(d = 3, flip = true);
    }
}
// Druckausrichtung: Einsatzlöcher nach oben
module leg_coupler() {
    if (is_floor)
        translate([0, 0, coupler / 2]) translate([-leg_split, 0, -leg_w / 2]) leg_coupler_asm();
}

// TPU-Fußkappe: flache Sohle unter der Fußfläche
module foot_pad() {
    fx = leg_w / cos(leg_angle);
    linear_extrude(height = pad_t) offset(r = 1) offset(delta = -1.5) square([fx, leg_w], center = true);
}

// ===========================================================================
// Normteile (werden nicht gedruckt – nur für die Passungsprüfung)
// ===========================================================================
module hw_socket_asm() {
    // Fassung (Mittelbohrung fürs Kabel), Gewinderohr und Mutter
    difference() {
        translate([0, 0, eps]) cylinder(d = socket_thread_d, h = socket_h);
        translate([0, 0, -1]) cylinder(d = 7.2, h = socket_h + 2);
    }
    difference() {
        translate([0, 0, -14]) cylinder(d = 10, h = 14 + socket_h / 2);
        translate([0, 0, -15]) cylinder(d = 7.1, h = 20 + socket_h);
    }
    difference() {
        translate([0, 0, -3]) cylinder(r = 14 / sqrt(3), h = 3 - eps, $fn = 6);
        translate([0, 0, -4]) cylinder(d = 10.1, h = 5);
    }
}

// ===========================================================================
// Zusammenbau
// ===========================================================================
module placed(id) {
    if (id == "hub") translate([0, 0, hub_z]) hub();
    if (id == "leg") for (k = [0 : 2]) translate([0, 0, hub_z]) leg_frame(k) translate([explode, 0, 0]) leg();
    if (id == "leg_upper") for (k = [0 : 2]) translate([0, 0, hub_z]) leg_frame(k) translate([explode, 0, 0]) leg_upper();
    if (id == "leg_lower") for (k = [0 : 2]) translate([0, 0, hub_z]) leg_frame(k)
        translate([2 * explode + leg_split, 0, 0]) leg_lower();
    if (id == "leg_coupler") if (is_floor) for (k = [0 : 2]) translate([0, 0, hub_z]) leg_frame(k)
        translate([1.5 * explode, 0, 0]) leg_coupler_asm();
    if (id == "foot_pad") for (k = [0 : 2]) let(th = leg_theta(k), r = hub_rs + leg_len * sin(leg_angle))
        translate([r * cos(th), r * sin(th), -explode / 2]) rotate(th) foot_pad();
    if (id == "body") translate([0, 0, col_z + explode]) body_asm();
    if (id == "twist_module") for (k = [0 : n_mod - 1])
        translate([0, 0, col_z + body_h + k * mod_h + (2 + k) * explode]) rotate(r_mod(k)) module_asm();
    if (id == "socket_cube") translate([0, 0, cube_z + (2 + n_mod) * explode]) rotate(r_cube) cube_asm();
    if (id == "shade_holder") translate([0, 0, cube_top + holder_z + (3 + n_mod) * explode]) rotate(r_cube) shade_holder();
    if (id == "shade") translate([0, 0, shade_z + (4 + n_mod) * explode]) rotate(r_cube) shade();
    // Normteile
    // Fassung dreht mit dem Würfel (Mutter sitzt in der Sechskanttasche)
    if (id == "hw_socket") translate([0, 0, cube_top + (2.5 + n_mod) * explode]) rotate(r_cube) hw_socket_asm();
    if (id == "hw_screws") for (k = [0 : 2]) translate([0, 0, hub_z]) leg_frame(k) {
        seat = -(boss_over - iso_socket_head(lock_screw)[1] - 0.2);   // Auflage des Kopfes
        translate([lock_x, 0, seat]) cylinder(d = lock_screw - 0.1, h = 12);
        translate([lock_x, 0, seat - iso_socket_head(lock_screw)[1]])
            cylinder(d = iso_socket_head(lock_screw)[0], h = iso_socket_head(lock_screw)[1] - eps);
    }
    if (id == "hw_magnets") for (s = [0 : 3]) rotate(r_cube + 90 * s) {
        translate([mag_r, 0, cube_top + holder_z + holder_t - magnet_h]) cylinder(d = magnet_d, h = magnet_h - eps);
        translate([mag_r, 0, shade_z + eps]) cylinder(d = magnet_d, h = magnet_h - eps);
    }
    if (id == "hw_cable") translate([0, 0, hub_z - 30]) cylinder(d = 6.3, h = cube_top - hub_z + 30 + socket_h / 2);
    if (id == "hw_bulb_clearance") translate([0, 0, cube_top + socket_h + eps])
        cylinder(r = bulb_d / 2 + bulb_gap, h = bulb_h);
}

part_ids = ["hub", "leg", "leg_upper", "leg_lower", "leg_coupler", "foot_pad", "body", "twist_module",
            "socket_cube", "shade_holder", "shade"];
hw_ids = ["hw_socket", "hw_screws", "hw_magnets", "hw_cable", "hw_bulb_clearance"];

module assembly() {
    for (id = part_ids) placed(id);
    %for (id = ["hw_socket"]) placed(id);
}

module all_parts() {
    translate([0, 0, 0]) shade();
    translate([150, 0, 0]) shade_holder();
    translate([150, 110, 0]) socket_cube();
    translate([230, 110, 0]) twist_module();
    translate([310, 110, 0]) body();
    translate([280, -20, 0]) hub();
    for (k = [0 : 2]) translate([-100, -120 - 35 * k, 0]) { leg(); leg_upper(); }
    for (k = [0 : 2]) translate([60, -120 - 35 * k, 0]) leg_lower();
    for (k = [0 : 2]) translate([220, -120 - 25 * k, 0]) leg_coupler();
    for (k = [0 : 2]) translate([-120 + 40 * k, 110, 0]) foot_pad();
}

// ===========================================================================
// Auswahl (ohne Standardzweig – SCAD Studio nutzt part = "__none__")
// ===========================================================================
if (part == "assembly") assembly();
else if (part == "all_parts") all_parts();
else if (part == "shade") shade();
else if (part == "shade_holder") shade_holder();
else if (part == "socket_cube") socket_cube();
else if (part == "twist_module") twist_module();
else if (part == "body") body();
else if (part == "hub") hub();
else if (part == "leg") leg();
else if (part == "leg_upper") leg_upper();
else if (part == "leg_lower") leg_lower();
else if (part == "leg_coupler") leg_coupler();
else if (part == "foot_pad") foot_pad();
else if (part == "hw_socket" || part == "hw_screws" || part == "hw_magnets" || part == "hw_cable"
         || part == "hw_bulb_clearance") placed(part);
