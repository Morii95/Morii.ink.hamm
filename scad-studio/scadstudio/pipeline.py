"""Arbeitsabläufe: KI-Konstruktion, Bildumwandlung, Rendern und Prüfen.

Jede Funktion läuft als Hintergrund-Auftrag (siehe jobs.py) und schreibt
ihren Fortschritt ins Auftragsprotokoll.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable

from . import openscad
from .ai import prompts
from .ai.providers import Image, Message, make_provider, guess_mime, GeminiProvider
from .config import Settings, app_home
from .jobs import Job, JobError
from .printers import printer_profile
from .projects import Project

# ---------------------------------------------------------------------------
# Rendern + Prüfen
# ---------------------------------------------------------------------------


def openscad_info(settings: Settings) -> openscad.OpenSCADInfo:
    info = openscad.detect(settings.get("openscad_path") or "")
    if not info.found:
        raise JobError(info.error or "OpenSCAD wurde nicht gefunden.")
    return info


def render_workers(settings: Settings) -> int:
    """Anzahl paralleler OpenSCAD-Prozesse (0 = automatisch: Kerne − 1, max. 4)."""
    try:
        n = int(settings.get("parallel_renders") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = max(1, min(4, (os.cpu_count() or 2) - 1))
    return n


def _parallel(items: Iterable[Any], fn: Callable[[Any], Any], workers: int):
    """Führt fn für alle Elemente parallel aus; liefert (Element, Ergebnis) in Fertig-Reihenfolge."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        for item in items:
            yield item, fn(item)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for fut in as_completed(futures):
            yield futures[fut], fut.result()


def _analyze(stl: Path, printer: dict[str, Any], *, bed_check: bool) -> dict[str, Any]:
    try:
        from . import meshcheck
    except ImportError:
        return {"ok": True, "issues": [], "size": None}
    bed = tuple(printer["bed"]) if bed_check else (1e9, 1e9, 1e9)
    try:
        result = meshcheck.analyze(stl, bed=bed, min_wall=printer["min_wall"],
                                   recommended_wall=printer["rec_wall"])
    except ValueError as exc:  # unlesbare/leere STL
        return {"ok": False, "size": None, "watertight": False,
                "issues": [{"level": "error", "text": str(exc), "code": "unreadable"}]}
    if not bed_check:
        result["bed"] = None
    return result


# Warnungen der Netzprüfung, die bei einem Druckteil repariert werden sollen
PART_REPAIR_CODES = {"bed_rotate", "thin_wall", "base", "flipped", "orientation",
                     "inverted_shells", "nonmanifold", "open_edges", "tiny"}


OVERHANG_REPAIR_FRACTION = 0.10   # ab 10 % Überhangfläche liegt das Teil vermutlich falsch


def _repair_issues(check: dict[str, Any], *, printable_part: bool) -> list[str]:
    """Fehler (immer) und – bei Druckteilen – relevante Warnungen der Netzprüfung."""
    out = []
    for issue in check.get("issues", []):
        level, code = issue.get("level"), issue.get("code", "")
        if level == "error" or (printable_part and level == "warning" and code in PART_REPAIR_CODES):
            out.append(issue["text"])
    overhang = check.get("overhang_fraction") or 0
    if printable_part and overhang > OVERHANG_REPAIR_FRACTION:
        out.append(f"{overhang * 100:.0f} % der Oberfläche hängen mehr als 45° über – das Teil muss "
                   "stützenfrei druckbar sein: im Export in Druckausrichtung legen (größte flache Seite "
                   "auf z=0) und Überhänge anfasen.")
    return out


def render_and_check(job: Job, project: Project, settings: Settings, *,
                     export_parts: bool = True, thumbnail: bool = True) -> dict[str, Any]:
    """Rendert das Modell, exportiert alle Druckteile und prüft jedes STL.

    Liefert u. a. `problems`: Liste verständlicher Probleme für die
    KI-Reparatur (leer = alles in Ordnung).
    """
    info = openscad_info(settings)
    printer = printer_profile(settings)
    use_manifold = bool(settings.get("use_manifold"))
    timeout = float(settings.get("render_timeout") or 600)
    code = project.read_code()
    # Normteile (hw_…) werden nicht gedruckt, nur bei der Passungsprüfung verwendet
    parts = [p for p in prompts.parse_parts(code) if not prompts.is_hardware(p["id"])]

    job.info("OpenSCAD rendert das Modell …" + (" (Manifold)" if use_manifold and info.manifold_flag else ""))
    res = openscad.render(project.scad_file, project.stl_file, info, use_manifold=use_manifold,
                          timeout=timeout, cancel=job.cancel_event)
    for w in res.warnings[:20]:
        job.warn(w)
    result: dict[str, Any] = {"ok": False, "render": res.to_dict(), "parts": [], "check": None,
                              "problems": []}
    if not res.ok:
        for e in res.errors[:20]:
            job.info(e, "error")
        result["problems"] = res.problems() or ["OpenSCAD konnte das Modell nicht rendern."]
        project.update(render=result["render"], check=None, parts=[], problems=result["problems"])
        return result
    job.info(f"Gerendert in {res.seconds:.1f} s.")
    problems = list(res.serious_warnings)

    check = _analyze(project.stl_file, printer, bed_check=not parts)
    result["check"] = check
    # Bei mehrteiligen Modellen ist der Zusammenbau nur Ansicht: nur Maße protokollieren
    _log_check(job, "Zusammenbau" if parts else "Gesamtmodell", check, issues=not parts)
    if not parts:
        # Einteiliges Modell: das Gesamtmodell ist das Druckteil.
        # Bei mehrteiligen Modellen zählen nur die einzelnen Druckteile (unten) –
        # im Zusammenbau berühren sich Teile gewollt.
        problems += _repair_issues(check, printable_part=True)

    if parts and export_parts:
        parts_dir = project.path / "parts"
        shutil.rmtree(parts_dir, ignore_errors=True)
        parts_dir.mkdir(exist_ok=True)   # unter Windows evtl. noch gesperrt
        workers = render_workers(settings)
        job.info(f"Exportiere {len(parts)} Druckteile" + (f" ({workers} parallel)" if workers > 1 else "") + " …")

        def export(part: dict[str, str]) -> openscad.RenderResult:
            job.check_cancel()
            return openscad.render(project.scad_file, parts_dir / f"{part['id']}.stl", info,
                                   use_manifold=use_manifold, timeout=timeout,
                                   defines={"part": f'"{part["id"]}"'}, cancel=job.cancel_event)

        rendered: dict[str, openscad.RenderResult] = {}
        for done, (part, pres) in enumerate(_parallel(parts, export, workers), 1):
            rendered[part["id"]] = pres
            job.set_progress(done / (len(parts) + 1))
            job.info(f"Druckteil {done}/{len(parts)} fertig: {part['label']} ({pres.seconds:.0f} s)")

        for part in parts:   # Auswertung in fester Reihenfolge
            out = parts_dir / f"{part['id']}.stl"
            pres = rendered[part["id"]]
            entry: dict[str, Any] = {"id": part["id"], "label": part["label"], "ok": pres.ok,
                                     "stl": f"parts/{part['id']}.stl" if pres.ok else "",
                                     "errors": pres.errors, "seconds": round(pres.seconds, 1)}
            if _only_empty(pres):
                # z. B. Teile einer anderen Variante (Tisch-/Stehlampe) – kein Fehler
                job.info(f"{part['label']}: in dieser Konfiguration nicht verwendet – übersprungen.")
                entry.update(ok=False, unused=True, errors=[])
                result["parts"].append(entry)
                continue
            if pres.ok:
                pcheck = _analyze(out, printer, bed_check=True)
                entry["check"] = pcheck
                _log_check(job, part["label"], pcheck)
                problems += [f"Teil „{part['id']}“: {t}"
                             for t in _repair_issues(pcheck, printable_part=True)]
            else:
                for e in pres.errors[:5]:
                    job.info(f"{part['label']}: {e}", "error")
                problems += [f"Teil „{part['id']}“: {e}" for e in (pres.problems() or ["Export fehlgeschlagen"])]
            result["parts"].append(entry)

    if thumbnail:
        try:
            openscad.render_png(project.stl_file, project.path / "preview.png", info, size=(480, 360),
                                timeout=120, cancel=job.cancel_event)
        except openscad.Cancelled:
            raise
        except Exception:
            pass

    result["problems"] = problems
    result["ok"] = True
    project.update(render=result["render"], check=check, parts=result["parts"], problems=problems)
    if problems:
        job.warn(f"Prüfung: {len(problems)} Hinweis(e) – siehe Prüfbericht.")
    else:
        job.info("Prüfung bestanden: Netz geschlossen, alle Teile passen aufs Druckbett.", "success")
    return result


def _only_empty(res: openscad.RenderResult) -> bool:
    """True, wenn OpenSCAD nur „Modell ist leer“ meldet (Teil hier nicht vorhanden)."""
    return (not res.ok and bool(res.errors)
            and all("leer" in e for e in res.errors) and not res.serious_warnings)


def _log_check(job: Job, name: str, check: dict[str, Any], issues: bool = True) -> None:
    size = check.get("size")
    if size:
        triangles = f"{check.get('triangles', 0):,}".replace(",", ".")
        job.info(f"{name}: {size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm, {triangles} Dreiecke")
    if not issues:
        return
    for issue in check.get("issues", []):
        level = {"error": "error", "warning": "warning"}.get(issue.get("level"), "info")
        job.info(f"{name}: {issue['text']}", level)


# ---------------------------------------------------------------------------
# Passungs- und Kollisionsprüfung
# ---------------------------------------------------------------------------

COLLISION_MIN_VOLUME = 0.5  # mm³ – kleinere Überschneidungen sind Rundungsrauschen


def has_placement(code: str) -> bool:
    return "module placed(" in code.replace(" (", "(")


def collision_check(job: Job, project: Project, settings: Settings) -> dict[str, Any]:
    """Setzt alle Teile wie im Zusammenbau zusammen und prüft paarweise, ob sie
    sich überschneiden. So lässt sich belegen, dass Gewinde, Stecker und
    Schrauben mit Spaltmaß passen, statt ineinander zu stecken.

    Voraussetzung: Das Modell hat ein Modul `placed(id)` (Teil in Einbaulage).
    """
    code = project.read_code()
    parts = prompts.parse_parts(code)
    if not parts or not has_placement(code):
        raise JobError("Für die Passungsprüfung braucht das Modell eine Teile-Liste (`part`) "
                       "und ein Modul `placed(id)`.")
    info = openscad_info(settings)
    use_manifold = bool(settings.get("use_manifold"))
    timeout = float(settings.get("render_timeout") or 600)
    work = project.path / "_passung"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(exist_ok=True)
    defines = {"part": '"__none__"', "explode": "0"}

    def render_snippet(name: str, body: str) -> openscad.RenderResult:
        # Hilfsdatei neben model.scad, damit relative Pfade (studio.scad) stimmen
        scad = project.path / f"_passung_{name}.scad"
        scad.write_text(f"include <model.scad>\n{body}\n", encoding="utf-8")
        try:
            return openscad.render(scad, work / f"{name}.stl", info, use_manifold=use_manifold,
                                   timeout=timeout, defines=defines, cancel=job.cancel_event)
        finally:
            _unlink_quietly(scad)

    try:
        return _collision_pairs(job, project, settings, parts, work, render_snippet)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for leftover in project.path.glob("_passung_*.scad"):
            _unlink_quietly(leftover)


def _collision_pairs(job: Job, project: Project, settings: Settings, parts: list[dict[str, str]],
                     work: Path, render_snippet: Callable[[str, str], openscad.RenderResult]) -> dict[str, Any]:
    from . import meshcheck

    # 0) Ohne Teil-Auswahl darf das Modell nichts erzeugen – sonst „kollidiert“ jedes Paar
    base = render_snippet("leer", "")
    if not _only_empty(base):
        raise JobError("Das Modell erzeugt auch ohne Teil-Auswahl (part = \"__none__\") Geometrie – "
                       "bitte keinen Standardzweig (else) im Dispatcher und keine Geometrie außerhalb "
                       "der Teile-Module verwenden.")

    workers = render_workers(settings)
    labels = {p["id"]: p["label"] for p in parts}
    incomplete: list[str] = []

    # 1) Jedes Teil in Einbaulage → Hüllquader
    job.info(f"Setze {len(parts)} Teile in Einbaulage" + (f" ({workers} parallel)" if workers > 1 else "") + " …")
    boxes: dict[str, dict[str, Any]] = {}
    placed_parts = _parallel(parts, lambda part: render_snippet(f"placed_{part['id']}", f'placed("{part["id"]}");'),
                             workers)
    for done, (part, res) in enumerate(placed_parts, 1):
        job.set_progress(done / (len(parts) * 2))
        if _only_empty(res):
            continue  # Teil wird in dieser Konfiguration nicht verwendet
        if not res.ok:
            job.warn(f"{part['label']}: Einbaulage konnte nicht erzeugt werden – nicht geprüft.")
            incomplete.append(f"{part['label']}: Einbaulage fehlerhaft ({(res.errors or ['?'])[0]})")
            continue
        boxes[part["id"]] = meshcheck.stl_bbox(work / f"placed_{part['id']}.stl")

    # 2) Nur Paare prüfen, deren Hüllquader sich berühren
    ids = [p["id"] for p in parts if p["id"] in boxes]
    pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:] if _boxes_touch(boxes[a], boxes[b])]
    # Teile, die im Zusammenbau nichts berühren, schweben – meist falsche Einbaulage
    floating = [labels[i] for i in ids if len(ids) > 1 and not any(i in pair for pair in pairs)]
    for label in floating:
        job.warn(f"{label}: berührt im Zusammenbau kein anderes Teil (schwebt?).")
    job.info(f"Prüfe {len(pairs)} Teilepaare, deren Hüllquader sich berühren …")
    collisions, checked = [], []

    def intersect(pair: tuple[str, str]) -> openscad.RenderResult:
        a, b = pair
        return render_snippet(f"pair_{a}__{b}", f'intersection() {{ placed("{a}"); placed("{b}"); }}')

    results = {}
    for done, (pair, res) in enumerate(_parallel(pairs, intersect, workers), 1):
        results[pair] = res
        job.set_progress(0.5 + done / (2 * max(1, len(pairs))))
        job.info(f"Passung {done}/{len(pairs)}: {labels[pair[0]]} ↔ {labels[pair[1]]} geprüft.")

    for a, b in pairs:   # Auswertung in fester Reihenfolge
        name = f"pair_{a}__{b}"
        res = results[(a, b)]
        entry: dict[str, Any] = {"a": a, "b": b, "label": f"{labels[a]} ↔ {labels[b]}"}
        if res.ok:
            volume = meshcheck.analyze(work / f"{name}.stl", wall_samples=0).get("volume_cm3", 0) * 1000
            entry["volume_mm3"] = round(volume, 2)
            entry["ok"] = volume < COLLISION_MIN_VOLUME
            if not entry["ok"]:
                # Lage der Überschneidung – die KI muss sonst selbst suchen, wo es klemmt
                region = meshcheck.stl_bbox(work / f"{name}.stl")
                entry["region"] = {k: [round(v, 1) for v in region[k]] for k in ("min", "max")}
        elif _only_empty(res):
            entry["volume_mm3"] = 0.0
            entry["ok"] = True
        else:
            entry["ok"] = None
            entry["error"] = (res.errors or ["Prüfung fehlgeschlagen"])[0]
            incomplete.append(f"{entry['label']}: nicht prüfbar ({entry['error']})")
        checked.append(entry)
        if entry["ok"] is False:
            collisions.append(entry)
            where = region_text(entry.get("region"))
            job.warn(f"Kollision: {entry['label']} überschneiden sich um {entry['volume_mm3']:.1f} mm³"
                     + (f" ({where})." if where else "."))
        elif entry["ok"]:
            job.info(f"{entry['label']}: frei (Spaltmaß eingehalten).")
        else:
            job.warn(f"{entry['label']}: konnte nicht geprüft werden.")

    result = {"ok": not collisions and not incomplete and not floating, "pairs": checked,
              "collisions": collisions, "incomplete": incomplete, "floating": floating,
              "parts": len(ids), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    project.update(collisions=result)
    if collisions:
        job.warn(f"{len(collisions)} Kollision(en) gefunden.")
    if incomplete:
        job.warn(f"Passungsprüfung unvollständig: {len(incomplete)} Teil(e)/Paar(e) nicht prüfbar.")
    if not collisions and not incomplete and not floating:
        job.info(f"Passungsprüfung bestanden: {len(checked)} Teilepaare ohne Überschneidung.", "success")
    return {"project": project.to_dict(), "collisions": result}


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def should_check_fit(settings: Settings, project: Project) -> bool:
    """Automatische Passungsprüfung: "on", "off" oder "auto" (nur mit schnellem Manifold)."""
    mode = str(settings.get("fit_check") or "auto")
    if mode == "off" or not has_placement(project.read_code()):
        return False
    if mode == "on":
        return True
    info = openscad.detect(settings.get("openscad_path") or "")
    return bool(info.manifold_flag and settings.get("use_manifold"))


def region_text(region: dict[str, list[float]] | None) -> str:
    """„x 10…30, y −5…5, z 0…12 mm“ – Bereich der Überschneidung in Einbaulage."""
    if not region:
        return ""
    return ", ".join(f"{axis} {lo:g}…{hi:g}" for axis, lo, hi in
                     zip("xyz", region["min"], region["max"])) + " mm"


def collision_problems(result: dict[str, Any]) -> list[str]:
    problems = []
    for c in result.get("collisions", []):
        where = region_text(c.get("region"))
        where = f" im Bereich {where} (Koordinaten des Zusammenbaus, placed())" if where else ""
        problems.append(f"Kollision im Zusammenbau: {c['label']} überschneiden sich um {c['volume_mm3']:.1f} mm³"
                        f"{where} – Spaltmaß, Position oder Gewindephase korrigieren.")
    problems += [f"Passungsprüfung unvollständig – {text}" for text in result.get("incomplete", [])]
    problems += [f"Teil „{label}“ berührt im Zusammenbau kein anderes Teil – es schwebt. Einbaulage "
                 f"(placed) und Verbindung prüfen." for label in result.get("floating", [])]
    return problems


def _boxes_touch(a: dict[str, Any], b: dict[str, Any], margin: float = 0.05) -> bool:
    return all(a["min"][i] - margin <= b["max"][i] and b["min"][i] - margin <= a["max"][i] for i in range(3))


# ---------------------------------------------------------------------------
# KI-Konstruktion
# ---------------------------------------------------------------------------


MAX_AI_IMAGE_SIDE = 2000          # px – größere Bilder bringen der KI nichts
MAX_AI_IMAGE_BYTES = 3_500_000    # Claude erlaubt max. 5 MB pro Bild (Base64 ≈ +33 %)


def prepare_ai_image(data: bytes) -> tuple[bytes, str]:
    """Bild für KI-Anbieter tauglich machen: gängiges Format, handliche Größe.

    Handyfotos (HEIC/BMP, 12 MP) würden sonst mit 400 abgelehnt.
    """
    mime = guess_mime(data)
    if mime in ("image/png", "image/jpeg", "image/webp") and len(data) <= MAX_AI_IMAGE_BYTES:
        try:
            from PIL import Image as PILImage
            with PILImage.open(io.BytesIO(data)) as probe:
                if max(probe.size) <= MAX_AI_IMAGE_SIDE:
                    return data, mime
        except Exception:
            return data, mime
    try:
        from PIL import Image as PILImage, ImageOps
        img = ImageOps.exif_transpose(PILImage.open(io.BytesIO(data)))
    except Exception:
        return data, mime   # Pillow kann es nicht lesen – Anbieter entscheidet
    img.thumbnail((MAX_AI_IMAGE_SIDE, MAX_AI_IMAGE_SIDE))
    buf = io.BytesIO()
    if img.mode in ("RGBA", "LA", "P") and "transparency" in img.info or img.mode in ("RGBA", "LA"):
        img.convert("RGBA").save(buf, "PNG", optimize=True)
        if buf.tell() <= MAX_AI_IMAGE_BYTES:
            return buf.getvalue(), "image/png"
        buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue(), "image/jpeg"


def _load_images(project: Project, names: list[str]) -> list[Image]:
    images = []
    for i, name in enumerate(names, 1):
        data, mime = prepare_ai_image(project.file(name).read_bytes())
        images.append(Image(data=data, mime=mime, label=f"Referenzbild {i}:"))
    return images


def _ask(job: Job, provider, system: str, messages: list[Message], what: str,
         purpose: str = "design") -> str:
    job.info(f"{what} ({provider.label()}) …")
    start = time.monotonic()
    try:
        text = provider.complete(system, messages, cancel=job.cancel_event, purpose=purpose)
    except TypeError:   # Anbieter ohne purpose-Parameter (z. B. Test-Attrappe)
        text = provider.complete(system, messages, cancel=job.cancel_event)
    job.info(f"Antwort nach {time.monotonic() - start:.0f} s erhalten.")
    return text


def _answer(provider, text: str) -> Message:
    """Antwort als Verlaufseintrag – inkl. Rohdaten, die manche Anbieter brauchen."""
    return Message("assistant", text, raw=getattr(provider, "last_raw", None))


def _run_ai_loop(job: Job, project: Project, settings: Settings, provider, system: str,
                 messages: list[Message], first_note: str) -> dict[str, Any]:
    repair_attempts = max(0, int(settings.get("repair_attempts") or 0))
    visual_rounds = max(0, int(settings.get("visual_rounds") or 0))
    has_refs = any(m.images for m in messages)

    text = _ask(job, provider, system, messages, "KI konstruiert das Modell")
    code, explanation = prompts.extract_code(text)
    if not code:
        messages.append(_answer(provider, text))
        messages.append(Message("user", "Bitte liefere jetzt die vollständige OpenSCAD-Datei in "
                                        "einem ```openscad Code-Block."))
        text = _ask(job, provider, system, messages, "Code wird nachgefordert", purpose="repair")
        code, explanation = prompts.extract_code(text)
        if not code:
            project.add_chat("assistant", text)
            raise JobError("Die KI hat keinen OpenSCAD-Code geliefert.")
    messages.append(_answer(provider, text))

    def apply(code: str, explanation: str, note: str) -> tuple[str, dict[str, Any]]:
        """Speichert, rendert, prüft und lässt Probleme reparieren."""
        attempt = 0
        while True:
            known = {v["n"] for v in project.meta.get("versions", [])}
            version = project.write_code(code, note)
            if version not in known:  # nur neue Stände im Verlauf zeigen
                project.add_chat("assistant", explanation or "Modell erstellt.", version=version)
            check = render_and_check(job, project, settings)
            problems = check["problems"]
            if not problems and should_check_fit(settings, project):
                try:
                    fit = collision_check(job, project, settings)["collisions"]
                    problems = collision_problems(fit)
                    check["problems"] = problems
                    project.update(problems=problems)
                except JobError as exc:
                    job.warn(f"Passungsprüfung übersprungen: {exc}")
            if not problems or attempt >= repair_attempts:
                if problems:
                    job.warn("Nicht alle Probleme konnten automatisch behoben werden – "
                             "Details im Prüfbericht. Du kannst eine Änderung beschreiben "
                             "oder den Code direkt bearbeiten.")
                return code, check
            attempt += 1
            job.info(f"Automatische Reparatur {attempt}/{repair_attempts}: "
                     f"{len(problems)} Problem(e) gehen zurück an die KI …")
            messages.append(Message("user", prompts.repair_text(problems)))
            text = _ask(job, provider, system, messages, "KI repariert", purpose="repair")
            new_code, new_expl = prompts.extract_code(text)
            messages.append(_answer(provider, text))
            if not new_code:
                job.warn("Die KI hat keinen korrigierten Code geliefert.")
                return code, check
            code, explanation, note = new_code, new_expl, f"Reparatur {attempt}"

    code, check = apply(code, explanation, first_note)

    for round_no in range(1, visual_rounds + 1):
        if not check.get("ok"):
            break
        job.check_cancel()
        composite = render_views(job, project, settings)
        if composite is None:
            break
        messages.append(Message("user", prompts.visual_text("isometrisch, vorne, oben", has_refs),
                                images=[Image(composite, "image/png", "Renderings des aktuellen Modells:")]))
        text = _ask(job, provider, system, messages,
                    f"KI prüft das Ergebnis visuell ({round_no}/{visual_rounds})", purpose="visual")
        messages.append(_answer(provider, text))
        if prompts.is_approval(text):
            job.info("Visuelle Selbstprüfung: Die KI ist mit dem Ergebnis zufrieden.", "success")
            project.add_chat("assistant", "Sichtprüfung: passt.")
            break
        new_code, new_expl = prompts.extract_code(text)
        if not new_code:
            # Mängel genannt, aber keine Datei geliefert → einmal nachfordern
            job.info("Sichtprüfung: " + " ".join(text.split())[:300])
            messages.append(Message("user", "Bitte behebe die genannten Mängel und liefere jetzt die "
                                            "vollständige verbesserte Datei in einem ```openscad Code-Block."))
            text = _ask(job, provider, system, messages, "Verbesserte Datei wird nachgefordert",
                        purpose="visual")
            messages.append(_answer(provider, text))
            new_code, new_expl = prompts.extract_code(text)
            if not new_code:
                project.add_chat("assistant", "Sichtprüfung: " + text.strip()[:1500])
                job.warn("Die KI hat nach der Sichtprüfung keine verbesserte Datei geliefert.")
                break
        job.info("Die KI verbessert das Modell nach der Sichtprüfung …")
        code, check = apply(new_code, new_expl, f"Sichtprüfung {round_no}")

    return {"project": project.to_dict(), "check": check}


def ai_generate(job: Job, project: Project, settings: Settings, instruction: str,
                image_names: list[str]) -> dict[str, Any]:
    provider = make_provider(settings)
    printer = printer_profile(settings)
    system = prompts.system_prompt(printer)
    images = _load_images(project, image_names)
    project.update(instruction=instruction, provider=provider.label(), images=image_names, changes=[])
    project.add_chat("user", instruction or "(nur Bilder)", images=image_names)
    messages = [Message("user", prompts.request_text(instruction, len(images)), images)]
    return _run_ai_loop(job, project, settings, provider, system, messages, "KI-Entwurf")


def ai_refine(job: Job, project: Project, settings: Settings, change: str,
              image_names: list[str]) -> dict[str, Any]:
    code = project.read_code()
    if not code.strip():
        raise JobError("Dieses Projekt hat noch keinen Code.")
    provider = make_provider(settings)
    system = prompts.system_prompt(printer_profile(settings))
    ref_names = list(project.meta.get("images") or []) + image_names
    images = _load_images(project, ref_names)
    changes = list(project.meta.get("changes") or [])
    text = prompts.refine_text(project.meta.get("instruction", ""), changes, code, change)
    project.add_chat("user", change, images=image_names)
    project.update(changes=changes + [change], images=ref_names)
    messages = [Message("user", text, images)]
    return _run_ai_loop(job, project, settings, provider, system, messages, f"Änderung: {change[:80]}")


def render_views(job: Job, project: Project, settings: Settings) -> bytes | None:
    """Drei Ansichten des STL als ein Bild (für die visuelle KI-Prüfung)."""
    try:
        from PIL import Image as PILImage, ImageDraw
    except ImportError:
        return None
    info = openscad_info(settings)
    tiles = []
    for view, label in (("iso", "Isometrisch"), ("front", "Vorne"), ("top", "Oben")):
        out = project.path / f"view_{view}.png"
        res = openscad.render_png(project.stl_file, out, info, view=view, size=(640, 520),
                                  timeout=180, cancel=job.cancel_event)
        if not res.ok:
            job.warn("Vorschaubilder konnten nicht erzeugt werden – visuelle Prüfung übersprungen.")
            return None
        tile = PILImage.open(out).convert("RGB")
        ImageDraw.Draw(tile).text((12, 10), label, fill=(255, 255, 255))
        tiles.append(tile)
    width = sum(t.width for t in tiles)
    sheet = PILImage.new("RGB", (width, max(t.height for t in tiles)), (30, 30, 30))
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0))
        x += tile.width
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    (project.path / "views.png").write_bytes(buf.getvalue())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Code direkt rendern
# ---------------------------------------------------------------------------


def render_code(job: Job, project: Project, settings: Settings, code: str | None,
                note: str = "Manuell bearbeitet") -> dict[str, Any]:
    if code is not None:
        version = project.write_code(code, note)
        job.info(f"Code gespeichert (Version {version}).")
    check = render_and_check(job, project, settings)
    return {"project": project.to_dict(), "check": check}


# ---------------------------------------------------------------------------
# Bild → 3D (ohne KI)
# ---------------------------------------------------------------------------

RELIEF_MODES = {"relief", "lithophane"}
SILHOUETTE_MODES = {"extrude", "plate", "keychain", "stamp", "cookie_cutter", "stencil"}


def image_to_model(job: Job, project: Project, settings: Settings, image_name: str,
                   mode: str, params: dict[str, Any]) -> dict[str, Any]:
    from . import generators, imaging

    data = project.file(image_name).read_bytes()
    img = imaging.load_image(data)
    title = project.meta.get("title", "Modell")
    if mode in RELIEF_MODES:
        job.info("Erzeuge Höhenkarte …")
        grid = imaging.heightmap(
            img, resolution=int(params.get("resolution", 200)),
            invert=bool(params.get("invert", mode == "lithophane")),
            blur=float(params.get("blur", 0)), gamma=float(params.get("gamma", 1.0)),
            auto_contrast=bool(params.get("auto_contrast", True)))
        code = generators.relief_scad(
            grid, width=float(params.get("width", 100)), depth=float(params.get("depth", 3)),
            base=float(params.get("base", 1)), mode=mode, frame=float(params.get("frame", 0)),
            title=title)
    elif mode in SILHOUETTE_MODES:
        job.info("Erkenne Umrisse …")
        mask = imaging.binary_mask(
            img, resolution=int(params.get("resolution", 400)),
            threshold=_opt_int(params.get("threshold")), invert=bool(params.get("invert", False)),
            use_alpha=str(params.get("use_alpha", "auto")), blur=float(params.get("blur", 0)),
            cleanup=int(params.get("cleanup", 1)))
        polygons = imaging.trace_contours(mask, simplify=float(params.get("simplify", 0.6)),
                                          smooth=int(params.get("smooth", 1)),
                                          min_area=float(params.get("min_area", 4)))
        if not polygons:
            raise JobError("Im Bild wurde keine Form erkannt – Schwellwert oder „Invertieren“ ändern.")
        job.info(f"{len(polygons)} Kontur(en), {sum(len(p) for p in polygons)} Punkte.")
        h, w = len(mask), len(mask[0])
        # nur bekannte Modus-Optionen weitergeben (z. B. ring_outer, stamp_thickness)
        options = {k: v for k, v in params.items() if k in generators.SILHOUETTE_OPTION_NAMES}
        code = generators.silhouette_scad(
            polygons, w, h, mode=mode, size=float(params.get("size", 80)),
            height=float(params.get("height", 3)), thicken=float(params.get("thicken", 0)),
            title=title, **options)
    else:
        raise JobError(f"Unbekannte Umwandlung: {mode}")
    version = project.write_code(code, f"Bild → {mode}")
    project.update(image_mode=mode, image_params=params, source_image=image_name)
    project.add_chat("assistant", f"Aus dem Bild erzeugt ({mode}).", version=version)
    check = render_and_check(job, project, settings)
    return {"project": project.to_dict(), "check": check}


def _opt_int(value: Any) -> int | None:
    if value in (None, "", "auto"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Bilder mit Gemini erzeugen
# ---------------------------------------------------------------------------

IMAGE_PRESETS: dict[str, dict[str, Any]] = {
    "frei": {"label": "Freier Prompt", "template": "{prompt}"},
    "silhouette": {
        "label": "Silhouette (für Anhänger, Schilder, Ausstechformen)",
        "template": ("{prompt}. Style: bold solid black silhouette on a pure white background, flat 2D "
                     "graphic, no shading, no gradients, no text, no thin hairlines, all parts connected "
                     "into one shape, centered with generous margin, very high contrast."),
    },
    "lineart": {
        "label": "Linienkunst / Tattoo-Flash (für Stempel, Reliefs)",
        "template": ("{prompt}. Style: clean black line art, tattoo flash style, on a pure white "
                     "background, bold uniform line weight (at least 1.5% of the image width), closed "
                     "shapes, no shading, no text, centered with margin."),
    },
    "tiefenkarte": {
        "label": "Tiefenkarte (für echtes 3D-Relief)",
        "template": ("A grayscale depth map (height map) of: {prompt}. White = closest to the viewer, black "
                     "= farthest / background. Smooth continuous gradients that describe the 3D shape, "
                     "no lighting, no shadows, no texture, no outlines, orthographic front view, object "
                     "centered on a pure black background."),
    },
    "bild_zu_tiefenkarte": {
        "label": "Hochgeladenes Bild → Tiefenkarte",
        "needs_image": True,
        "template": ("Convert this image into a grayscale depth map for a 3D bas-relief: white = nearest to "
                     "the viewer, black = farthest / background. Keep the exact outline, pose and "
                     "composition; remove colors, lighting, shadows and texture; smooth gradients. {prompt}"),
    },
    "produkt": {
        "label": "Produktansicht (Vorlage für KI-Konstruktion)",
        "template": ("A clean studio product render of {prompt} as a reference for CAD modelling: simple "
                     "clear geometric forms, neutral light-gray matte material, plain white background, "
                     "soft even lighting, 3/4 isometric view, the entire object visible."),
    },
    "technisch": {
        "label": "Technische Zeichnung mit Maßen",
        "template": ("A clean orthographic technical drawing sheet of {prompt} with front, side and top views "
                     "and dimension lines in millimetres, black lines on white, no shading."),
    },
}


def generate_image(job: Job, settings: Settings, prompt: str, preset: str,
                   input_images: list[bytes], aspect_ratio: str = "1:1") -> dict[str, Any]:
    spec = IMAGE_PRESETS.get(preset, IMAGE_PRESETS["frei"])
    if spec.get("needs_image") and not input_images:
        raise JobError("Für diese Vorlage bitte zuerst ein Bild hochladen.")
    text = spec["template"].format(prompt=prompt.strip()).strip()
    provider = GeminiProvider(settings.get("gemini_api_key"), settings.get("gemini_model"))
    model = settings.get("gemini_image_model")
    job.info(f"Gemini erzeugt das Bild ({model}) …")
    images = [Image(d, guess_mime(d)) for d in input_images]
    data, note = provider.generate_image(text, images, model, aspect_ratio, cancel=job.cancel_event)
    folder = app_home() / "bilder"
    folder.mkdir(exist_ok=True)
    mime = guess_mime(data)
    ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{preset}.{ext}"
    path.write_bytes(data)
    job.info(f"Bild gespeichert: {path}", "success")
    return {"image": f"data:{mime};base64," + base64.b64encode(data).decode("ascii"),
            "note": note, "file": str(path), "prompt": text}
