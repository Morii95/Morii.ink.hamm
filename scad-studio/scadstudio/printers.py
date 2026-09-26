"""Drucker-Profile (Bauraum in mm).

Das Profil fließt in den KI-Prompt, in die Customizer-Variablen der
erzeugten Dateien und in die Druckbett-Prüfung jedes Teils ein.
"""

from __future__ import annotations

from typing import Any

# id: (Name, X, Y, Z)
PRINTERS: dict[str, tuple[str, float, float, float]] = {
    "kobra2neo": ("Anycubic Kobra 2 Neo", 220, 220, 250),
    "kobra2": ("Anycubic Kobra 2 / 2 Pro", 220, 220, 250),
    "kobra2plus": ("Anycubic Kobra 2 Plus", 320, 320, 400),
    "kobra2max": ("Anycubic Kobra 2 Max", 420, 420, 500),
    "kobra3": ("Anycubic Kobra 3", 250, 250, 260),
    "kobra3max": ("Anycubic Kobra 3 Max", 420, 420, 500),
    "kobras1": ("Anycubic Kobra S1", 250, 250, 250),
    "kobras1max": ("Anycubic Kobra S1 Max", 350, 350, 350),
    "bambu_a1mini": ("Bambu Lab A1 mini", 180, 180, 180),
    "bambu_a1": ("Bambu Lab A1", 256, 256, 256),
    "bambu_p1s": ("Bambu Lab P1S / P2S / X1C", 256, 256, 256),
    "bambu_h2d": ("Bambu Lab H2D (eine Düse)", 325, 320, 325),
    "bambu_h2s": ("Bambu Lab H2S", 340, 320, 340),
    "prusa_mk4s": ("Prusa MK4S", 250, 210, 220),
    "prusa_coreone": ("Prusa CORE One", 250, 220, 270),
    "prusa_mini": ("Prusa MINI+", 180, 180, 180),
    "ender3v3se": ("Creality Ender-3 V3 SE", 220, 220, 250),
    "creality_k1": ("Creality K1", 220, 220, 250),
    "creality_k2": ("Creality K2", 260, 260, 260),
    "creality_k2pro": ("Creality K2 Pro", 300, 300, 300),
    "creality_k2plus": ("Creality K2 Plus", 350, 350, 350),
    "custom": ("Eigener Drucker", 220, 220, 250),
}

DEFAULT_PRINTER = "kobra2neo"


def printer_profile(settings: Any) -> dict[str, Any]:
    """Aktives Profil inkl. Düse, Spaltmaß und Material aus den Einstellungen."""
    pid = settings.get("printer") or DEFAULT_PRINTER
    name, x, y, z = PRINTERS.get(pid, PRINTERS[DEFAULT_PRINTER])
    if pid == "custom":
        x = float(settings.get("bed_x") or x)
        y = float(settings.get("bed_y") or y)
        z = float(settings.get("bed_z") or z)
    nozzle = _float(settings.get("nozzle"), 0.4)
    return {
        "id": pid,
        "name": name,
        "bed": [x, y, z],
        "nozzle": nozzle,
        "layer": _float(settings.get("layer_height"), 0.2),
        "tol": _float(settings.get("tolerance"), 0.2),
        "material": settings.get("material") or "PLA",
        # 2 Linienbreiten als Minimum, 3–4 als Empfehlung
        "min_wall": round(2 * nozzle * 1.1, 2),
        "rec_wall": round(4 * nozzle * 1.1, 2),
    }


def printer_list() -> list[dict[str, Any]]:
    return [{"id": pid, "name": v[0], "bed": [v[1], v[2], v[3]]} for pid, v in PRINTERS.items()]


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
