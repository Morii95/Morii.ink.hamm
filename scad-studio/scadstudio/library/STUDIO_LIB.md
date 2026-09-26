# studio.scad – connector & printing library (API reference for the LLM)

`use <studio.scad>` (modules/functions only). Units mm/degrees. Works in OpenSCAD 2021.01 and 2025+ (Manifold).
Target printer: Anycubic Kobra 2 Neo, bed 220×220×250, 0.4 nozzle, PLA. **Use this library for every thread,
screw, nut, insert, magnet, plug and snap joint instead of writing your own.**

## Conventions (read first)
- Globals (set once at top of the model): `$tol = 0.2;` clearance per side (radial), `$nozzle = 0.4;`,
  resolution `$fa = 4; $fs = 0.4;` or `$fn = 64;` (threads cap themselves at 72 segments/turn).
- **Positive parts** (thread_male, bolt, peg, plugs, dovetail, snap_hook, bayonet_male) stand on z=0, grow +Z,
  centered on the Z axis → `union()` them onto a face at z=0.
- **Negative parts** (all `*_hole`, `*_socket`, `*_slot`, `*_pocket`, `*_trap`, `*_window`, `thread_female`,
  `metric_thread(internal=true)`, `screw_hole`, `cable_*`, `lamp_nipple_mount`) go inside `difference()`.
  Entry face is z=0, they extend +Z and overshoot 0.05 mm below z=0 (`ext`). `flip=true` rotates them 180° about X
  so they go −Z (cut into a TOP face: `translate([x,y,top]) screw_hole(..., flip=true)`). Through-type shapes
  (screw_hole, lamp_nipple_mount, teardrop_hole, cable_channel) also overshoot at the far end.
- **Pass the SAME nominal numbers to male and female.** Female shapes add `tol` everywhere (radius and depth);
  male shapes are nominal. Holes are circumscribed polygons (never undersized).
- Mating frame: in an assembly the male's z=0 shoulder and the female's z=0 entry face are the same plane.

## Threads (ISO metric 60°, one closed polyhedron, fast)
- `metric_thread(d=10, pitch=iso_pitch(d), length=10, internal=false, tol=$tol, lead_in=true, fn, left=false, profile="iso", phase=0, seated=true, bore=0)` – external (positive) or `internal=true` cutter (negative, radially + axially enlarged by tol). `lead_in` = bool or `[bottom, top]` 45° chamfers. `profile="printable"` = 90° thread (45° flanks, printed-to-printed only).
- `threaded_rod(d=8, length=30, pitch, lead_in=true, fn, left, profile, phase, bore=0)` – both ends chamfered.
- `bolt(d=8, length=20, head="hex"|"socket"|"countersunk"|"button"|"none", pitch, thread_length, fn, left, profile, tol)` – head on z=0..k (print head-down), thread up; `length` under the head (countersunk: overall, ISO).
- `nut(d=8, height=ISO 4032 m, pitch, tol, s=ISO s, fn, left, profile, chamfer=true)` – hex nut with internal thread.
- `threaded_hole(d=8, depth=10, pitch, tol, lead_in=true, through=false, fn, left, profile, phase=0, seated=true, flip=false)` – negative.
- `thread_male(d=24, pitch, length=15, lead_in=true, fn, left, profile, phase=0, bore=0, flip=false)` – spigot for joining two printed parts; only the top is chamfered; `bore` = coaxial through hole (cable).
- `thread_female(d=24, pitch, length=15, tol, lead_in=true, fn, left, profile, phase=0, seated=true, flip=false)` – matching negative, depth = length + max(0.5, 2·tol), mouth chamfer at z=0.
- **Phase/alignment:** `phase` = angle where the crest (male) / groove (female) centre crosses the local z=0 plane.
  With equal `phase` and `seated=true` (default), a male screwed in until its shoulder touches the female face ends
  at 0° relative rotation (within ≈360·0.05/P°, 6° for P=3) → square modules stay flush. Different phases: female ends
  rotated by (phase_m − phase_f). Works for any length (reference is the mating plane, not the thread end).
- Rules: print thread axis vertical; printed threads ≥ M8, better ≥ M12 with pitch 1.5–3 (M24×3 ideal for lamp
  segments); ≥ 4 turns engagement; wall around a female thread ≥ `thread_wall(d)`; for M3–M6 use `heatset_hole` or
  `nut_trap` + metal screw instead of printed threads. Right-hand; tighten clockwise seen from the screwing side.

## Fasteners (negative, ISO tables built in)
- `screw_hole(d=3, length=10, head="socket"|"button"|"countersunk"|"hex"|"none", head_depth, tol, fit="medium", sacrificial=0, flip=false)` – shaft Ø = ISO 273 medium + tol, through `length`; head recess at z=0: socket = DIN 974 counterbore (M3 6.5, M4 8, M5 10, M6 11, M8 15), depth k+tol (flush); countersunk = 90° cone at ISO 10642 dk+2·tol (head_depth adds depth); hex = ISO 4017 head pocket. `sacrificial=0.2` leaves a bridge layer over the counterbore (drill out).
- `nut_trap(d=3, depth=m+tol+0.1, tol, slot=0, s, flip=false)` – ISO 4032 hex pocket (flats ∥ X), across flats s+2·tol; `slot` = side insertion slot length toward +X.
- `heatset_hole(d=3, depth=L+1, hole_d, chamfer=0.3, flip=false)` – heat-set insert (Ruthex/CNC Kitchen): hole Ø M2 3.2, M2.5 4.0, M3 4.0, M4 5.6, M5 6.4, M6 8.0, M8 9.7; lengths M2 4, M2.5/M3 5.7, M4 8.1, M5 9.5, M6/M8 12.7. Wall around insert ≥ `heatset_wall(d)` (1.5 M3 / 2.0 M4 / 2.5 M5+).
- `magnet_pocket(d=6, h=3, tol=0.1, depth_extra=0.15, flip=false)` – Ø d+2·tol (tol 0.05 press fit, 0.1–0.2 glue), depth h+0.15.
- `lamp_nipple_mount(length=10, af=14, nut_h=3, nipple_d=10, tol, nut_pocket=true, flip=false)` – M10×1 lamp nipple: through hole Ø10.4 + hex pocket for the lamp nut (AF 14.4 × 3.3 at z=0). AF 13 nuts: `af=13`.

## Plugs & alignment (female side gets tol)
- `peg(d=5, h=6, chamfer=0.5)` / `peg_hole(d=5, depth=6, tol, chamfer=0.3, flip)` – round pin; pins 3–5 mm.
- `d_peg(d=6, h=8, flat=0.15·d, chamfer=0.5)` / `d_peg_hole(d=6, depth=8, flat, tol, chamfer=0.3, flip)` – anti-rotation D, flat faces +X.
- `square_plug(size=10|[x,y], h=10, chamfer=0.5, r=0)` / `square_socket(size, depth=10, tol, r=0, chamfer=0.3, flip)` – tenon/mortise, centered, optional corner radius r.
- `dovetail(w=10, h=5, length=20, angle=15)` / `dovetail_slot(w, h, length, angle=15, tol, flip)` – rail along Y (centered), narrow side w at z=0; slot is length+2·tol long → let it run out of the part to slide in.
- `snap_hook(length=12, width=6, thickness=max(1.2,L/8), undercut=auto(2 % strain), lead_angle=30, land, fillet=t/2, strain=0.02)` – beam x∈[−t,0], barb toward +X. `snap_window(same params…, tol, wall=3)` – window cut in the hook's frame; mating wall inner face at x≥0, starting above z=fillet.
- `bayonet_male(d=30, h=10, lugs=3, lug_w=6, lug_h=3, lug_depth=1.5, lug_z)` / `bayonet_socket(same…, twist=30, tol, flip)` – insert at 0°, then turn the socket part clockwise (seen from above) by `twist` to lock.

## Utilities
- `rounded_box(size=[20,20,10], r=2, center=false, edges="z"|"top"|"all")` – cube semantics; "top" keeps a flat bottom.
- `chamfer_cylinder(d=10, h=10, chamfer_bottom=0, chamfer_top=0, center=false)` – bottom 0.4–0.6 fights elephant foot.
- `teardrop_hole(d=5, length=10, tol, center=false, truncate=false)` – horizontal hole along +X from x=0, tip +Z, Ø d+2·tol (tol=0 for exact).
- `cable_channel(d=6, length=20, clearance=2, teardrop=true, center=false, truncate=false)` – along +X, Ø d+clearance (cable H03VV-F 2×0.75 ≈ 6 mm → ≥ 8 mm channel).
- `cable_path(points, d=6, clearance=2)` – sphere-hull chain through 3D points (bends: radius ≥ 6×cable Ø, use several points).
- `elephant_foot_extrude(height=10, chamfer=0.4, steps=2) <2D children>` – linear_extrude with stepped bottom chamfer.
- `fits_bed(size, bed=[220,220,250], margin=0)` → bool (XY may rotate 90°). `bed_outline(bed=[220,220,250], center=true)` – %-preview frame only.

## Print rules (FDM, PLA)
- Threads, pegs, sockets: axis vertical. A female thread may open onto the bed (mouth chamfer absorbs elephant foot) or
  face up; its blind end is a bridge. Horizontal holes/channels → `teardrop_hole` / `cable_channel`.
- Snap fits: strain 1.5–2 % (`undercut` auto = 0.67·ε·L²/t), L/t ≈ 8–10, t ≥ 1.2 mm, keep the root fillet.
- Dovetail flank 10–15°, clearance 0.1–0.2/side; alignment pins Ø3–5 with 0.2–0.25/side; D-flat 10–15 % of Ø.
- Magnets: depth = thickness + 0.1–0.2; alternate polarity between parts. Glue fit tol 0.1–0.2, press fit 0.05.

## Functions
`iso_pitch(d)` (M1–M64, coarse), `iso_clearance(d, fit="medium")`, `iso_nut_s(d)`, `iso_nut_m(d)`, `iso_socket_head(d)`→[dk,k,s,t],
`iso_csk_head(d)`→[dk,k,s], `iso_button_head(d)`, `iso_hex_head(d)`→[s,k], `counterbore_d(d)`, `heatset_dims(d)`→[hole_d,len],
`heatset_wall(d)`, `thread_minor_d(d,pitch)`, `thread_pitch_d(d,pitch)`, `thread_wall(d)`, `studio_version()`.

## Examples
```openscad
// 1) Two square lamp segments joined by a printed M24×3 thread (faces align when tight)
use <studio.scad>
$fa = 4; $fs = 0.4;
module segment_a() difference() {          // male spigot on top
    union() { translate([-20, -20, 0]) cube([40, 40, 60]);
              translate([0, 0, 60]) thread_male(d = 24, pitch = 3, length = 12); }
    translate([0, 0, -1]) cylinder(d = 8, h = 80);                    // cable bore
}
module segment_b() difference() {          // female thread in the bottom face
    translate([-20, -20, 0]) cube([40, 40, 60]);
    thread_female(d = 24, pitch = 3, length = 12);
    translate([0, 0, -1]) cylinder(d = 8, h = 80);
}
segment_a(); translate([50, 0, 0]) segment_b();
```
```openscad
// 2) Leg with square plug (0.2 gap) + locking screw M3 into a heat-set insert
use <studio.scad>
$tol = 0.2; $fa = 4; $fs = 0.4;
difference() {                              // leg 30×30×150, plug 16×16×20 on top
    union() { translate([-15, -15, 0]) cube([30, 30, 150]); translate([0, 0, 150]) square_plug(16, 20); }
    translate([8, 0, 160]) rotate([0, -90, 0]) heatset_hole(3);        // insert from the plug's +X face
}
translate([60, 0, 0]) difference() {        // top block, bottom face sits on the leg shoulder
    translate([-25, -25, 0]) cube([50, 50, 30]);
    square_socket(16, 20);                                             // 16.4² × 20.2 deep
    translate([25, 0, 10]) rotate([0, -90, 0]) screw_hole(3, 17, head = "socket");  // M3×16 screw
}
```
```openscad
// 3) Magnet pockets: shade ring (pockets in its bottom face) + base (pockets in its top face)
use <studio.scad>
$fa = 4; $fs = 0.4;
difference() { cylinder(d = 120, h = 5); translate([0, 0, -1]) cylinder(d = 100, h = 7);
    for (a = [45 : 90 : 315]) rotate(a) translate([55, 0, 0]) magnet_pocket(6, 3); }
translate([130, 0, 0]) difference() { cylinder(d = 120, h = 10);
    for (a = [45 : 90 : 315]) rotate(a) translate([55, 0, 10]) magnet_pocket(6, 3, flip = true); }
```
```openscad
// 4) Lamp base: cable enters at the back, bends up (R15) to an M10×1 nipple mount on top
use <studio.scad>
$fa = 4; $fs = 0.4;
assert(fits_bed([80, 80, 30]));
difference() {
    rounded_box([80, 80, 30], r = 6, edges = "top");
    translate([40, 40, 30]) lamp_nipple_mount(length = 12, flip = true);
    translate([40, 55, 10]) rotate(90) cable_channel(6, 27);          // straight part, teardrop
    cable_path([for (a = [0 : 15 : 90]) [40, 55 - 15 * sin(a), 25 - 15 * cos(a)]], d = 6);
}
bed_outline();
```
