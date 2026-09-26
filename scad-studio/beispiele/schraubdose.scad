/* ===========================================================================
   Schraubdose mit Gewindedeckel (Passungstest)
   Runde Dose mit gedrucktem Gewinde (M69×3, stützenfreies 45°-Profil) – ideal, um vor großen Projekten das
   Spaltmaß deines Druckers zu testen (Standard 0,2 mm). Erzeugt mit SCAD Studio.

   Druckteile:
     jar   Dose    aufrecht, keine Stützen
     lid   Deckel  Oberseite nach unten (liegt so bereits im Export)
   Empfohlen: 0,2 mm Schichten, 3 Wände, 15 % Infill. Deckel geht zu schwer?
   → `tol` um 0,05 erhöhen. Wackelt er? → `tol` verringern.
   In OpenSCAD: Fenster → Customizer für alle Parameter.
   =========================================================================== */

use <studio.scad>;

/* [Ansicht] */
part = "assembly"; // [assembly:Zusammenbau, all_parts:Alle Teile (Druckansicht), jar:Dose, lid:Deckel]
explode = 0; // [0:5:60] Explosionsansicht in mm

/* [Maße] */
inner_d = 60;      // [20:1:150] Innendurchmesser
height = 60;       // [20:1:200] Höhe der Dose (mit Gewinde)
wall = 2.4;        // [1.2:0.4:4] Wandstärke
floor_t = 2;       // [1:0.2:4] Bodenstärke
thread_len = 10;   // [6:1:20] Gewindelänge (≥ 3 Gänge)
pitch = 3;         // [2:0.5:4] Gewindesteigung
thread_profile = "printable"; // [printable:Druckoptimiert (45°-Flanken), iso:ISO 60°]
lid_top = 2.4;     // [1.2:0.4:4] Deckelstärke
grip_ribs = 36;    // [0:2:72] Griffrillen am Deckel

/* [Drucker] */
bed = [220, 220, 250]; // Bauraum X, Y, Z in mm (Anycubic Kobra 2 Neo)
nozzle = 0.4;          // [0.2:0.1:1.0]
tol = 0.2;             // [0.05:0.05:0.6] Spaltmaß pro Seite

/* [Qualität] */
$fa = 3;
$fs = 0.5;

/* [Hidden] */
$tol = tol;
$nozzle = nozzle;
eps = 0.01;
thread_d = ceil(inner_d + 2 * wall + 2 * 0.62 * pitch);   // Außen-Ø des Gewindes
lid_d = thread_d + 2 * (wall + tol + 0.5);                 // Außen-Ø Deckel = Dose
shoulder = height - thread_len;
lid_h = thread_len + 0.5 + 2 * tol + lid_top;

assert(lid_d + 2 <= min(bed[0], bed[1]), "Dose passt nicht aufs Druckbett");
assert(height <= bed[2], "Dose zu hoch für den Drucker");
echo(str("Dose Ø ", lid_d, " mm, Gewinde M", thread_d, "×", pitch, ", Innenraum Ø ", inner_d, " mm"));

// Dose: Körper bis zur Schulter, darüber das Außengewinde
module jar() {
    difference() {
        union() {
            chamfer_cylinder(d = lid_d, h = shoulder, chamfer_bottom = 0.6, chamfer_top = 0);
            translate([0, 0, shoulder - eps])
                thread_male(d = thread_d, pitch = pitch, length = thread_len + eps, bore = inner_d,
                            profile = thread_profile);
        }
        translate([0, 0, floor_t]) cylinder(d = inner_d, h = height);
    }
}

// Deckel in Einbaulage (Unterkante z=0 = Schulter der Dose)
module lid_asm() {
    difference() {
        linear_extrude(height = lid_h, convexity = 4)
            difference() {
                circle(d = lid_d);
                if (grip_ribs > 0)
                    for (i = [0 : grip_ribs - 1]) rotate(i * 360 / grip_ribs)
                        translate([lid_d / 2, 0]) circle(d = 1.6, $fn = 12);
            }
        thread_female(d = thread_d, pitch = pitch, length = thread_len, profile = thread_profile);
    }
}
module lid() translate([0, 0, lid_h]) rotate([180, 0, 0]) lid_asm();

module placed(id) {
    if (id == "jar") jar();
    if (id == "lid") translate([0, 0, shoulder + explode]) lid_asm();
}
module assembly() for (id = ["jar", "lid"]) placed(id);
module all_parts() { jar(); translate([lid_d + 10, 0, 0]) lid(); }

if (part == "assembly") assembly();
else if (part == "all_parts") all_parts();
else if (part == "jar") jar();
else if (part == "lid") lid();
