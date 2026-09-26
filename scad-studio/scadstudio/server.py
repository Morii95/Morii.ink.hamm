"""Lokaler Webserver: liefert die Oberfläche aus und stellt die JSON-API bereit.

Sicherheit: Der Server lauscht nur auf 127.0.0.1, prüft den Host-Header
(Schutz vor DNS-Rebinding) und verlangt für schreibende Aufrufe einen
eigenen Header. Fremde Webseiten können die API deshalb nicht benutzen.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import mimetypes
import os
import re
import threading
import urllib.parse
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import APP_NAME, __version__, openscad, pipeline
from .ai.providers import PROVIDER_LABELS, GeminiProvider, make_provider, provider_status
from .config import (ANTHROPIC_MODELS, GEMINI_IMAGE_MODELS, GEMINI_MODELS, OPENAI_MODEL_HINTS,
                     Settings, app_home)
from .jobs import JobError, JobManager
from .printers import printer_list, printer_profile
from .projects import ProjectStore

STATIC_DIR = Path(__file__).parent / "static"
EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "beispiele"
MAX_BODY = 60 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".stl": "model/stl",
    ".3mf": "model/3mf",
    ".scad": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def decode_data_url(value: str) -> tuple[bytes, str]:
    """data:image/png;base64,... → (Bytes, Dateiendung)."""
    match = re.match(r"^data:([\w/+.\-]+)?(;base64)?,(.*)$", value or "", re.S)
    if not match or not match.group(2):
        raise ApiError("Ungültige Bilddaten.")
    try:
        data = base64.b64decode(match.group(3), validate=False)
    except (binascii.Error, ValueError):
        raise ApiError("Bilddaten konnten nicht gelesen werden.") from None
    if len(data) > MAX_IMAGE:
        raise ApiError("Bild ist zu groß (max. 25 MB).")
    mime = (match.group(1) or "image/png").lower()
    ext = {"image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp"}.get(mime, "png")
    return data, ext


class Studio:
    """Hält den Zustand der App (Einstellungen, Projekte, Aufträge)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.jobs = JobManager()
        self.project_jobs: dict[str, str] = {}   # Projekt → letzter Auftrag
        self._store: ProjectStore | None = None
        self._store_root: Path | None = None
        self._lock = threading.Lock()

    def running_job(self, project_id: str) -> str | None:
        job_id = self.project_jobs.get(project_id)
        job = self.jobs.get(job_id) if job_id else None
        return job.id if job and job.status == "running" else None

    @property
    def store(self) -> ProjectStore:
        root = self.settings.projects_dir()
        with self._lock:
            if self._store is None or self._store_root != root:
                self._store = ProjectStore(root)
                self._store_root = root
            return self._store

    def status(self) -> dict[str, Any]:
        info = openscad.detect(self.settings.get("openscad_path") or "")
        try:
            projects_dir, projects_error = str(self.settings.projects_dir()), ""
        except OSError as exc:  # z. B. USB-Stick entfernt – Oberfläche muss trotzdem laufen
            projects_dir, projects_error = "", f"Projektordner nicht verfügbar: {exc}"
        return {
            "app": APP_NAME,
            "version": __version__,
            "openscad": info.to_dict(),
            "providers": provider_status(self.settings),
            "provider_labels": PROVIDER_LABELS,
            "printer": printer_profile(self.settings),
            "printers": printer_list(),
            "image_presets": {k: {"label": v["label"], "needs_image": bool(v.get("needs_image"))}
                              for k, v in pipeline.IMAGE_PRESETS.items()},
            "models": {"gemini": GEMINI_MODELS, "gemini_image": GEMINI_IMAGE_MODELS,
                       "anthropic": ANTHROPIC_MODELS, "openai": OPENAI_MODEL_HINTS},
            "settings": self.settings.public(),
            "home": str(app_home()),
            "projects_dir": projects_dir,
            "projects_error": projects_error,
        }


def make_handler(studio: Studio) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"SCADStudio/{__version__}"
        protocol_version = "HTTP/1.1"

        # -- Hilfen ----------------------------------------------------------
        def log_message(self, fmt: str, *args: Any) -> None:  # leise
            pass

        def _allowed_host(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
            return name in ("127.0.0.1", "localhost", "[::1]")

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, message: str, status: int = 400) -> None:
            self._json({"error": message}, status)

        def _read_body(self) -> bytes | None:
            """Liest den Rumpf immer vollständig (sonst stört der Rest die nächste Anfrage)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self.close_connection = True
                return None
            return self.rfile.read(length) if length else b""

        def _body(self, raw: bytes) -> dict[str, Any]:
            raw = raw or b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                raise ApiError("Ungültiges JSON.") from None
            if not isinstance(data, dict):
                raise ApiError("Ungültige Anfrage.")
            return data

        def _file(self, path: Path, download: bool = False) -> None:
            if not path.is_file():
                self._error("Datei nicht gefunden.", 404)
                return
            ctype = CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] \
                or "application/octet-stream"
            extra = {}
            if download:
                extra["Content-Disposition"] = f'attachment; filename="{path.name}"'
            self._send(200, path.read_bytes(), ctype, extra)

        # -- Routing ---------------------------------------------------------
        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            if not self._allowed_host():
                self._error("Unzulässiger Host.", 403)
                return
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            path = urllib.parse.unquote(url.path)
            try:
                if path in ("/", "/index.html"):
                    self._file(STATIC_DIR / "index.html")
                elif path.startswith("/static/"):
                    target = (STATIC_DIR / path[len("/static/"):]).resolve()
                    if STATIC_DIR.resolve() not in target.parents:
                        raise ApiError("Nicht gefunden.", 404)
                    self._file(target)
                elif path.startswith("/files/"):
                    _, _, project_id, rest = path.split("/", 3)
                    project = studio.store.get(project_id)
                    self._file(project.file(rest), download="download" in query)
                elif path.startswith("/zip/"):
                    self._zip(path.split("/", 2)[2])
                elif path.startswith("/api/"):
                    self._json(self._get_api(path[5:], query))
                else:
                    raise ApiError("Nicht gefunden.", 404)
            except ApiError as exc:
                self._error(str(exc), exc.status)
            except (KeyError, ValueError) as exc:
                self._error(f"Nicht gefunden: {exc}", 404)
            except Exception as exc:  # letzte Absicherung
                self._error(f"Interner Fehler: {exc}", 500)

        def do_POST(self) -> None:
            raw = self._read_body()
            if raw is None:
                self._error("Anfrage zu groß.", 413)
                return
            if not self._allowed_host():
                self._error("Unzulässiger Host.", 403)
                return
            # Eigener Header: erzwingt bei fremden Webseiten einen CORS-Preflight,
            # den dieser Server nie beantwortet.
            if self.headers.get("X-Studio") != "1":
                self._error("Fehlender Header.", 403)
                return
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlsplit(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                self._error("Unzulässige Herkunft.", 403)
                return
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            try:
                if not path.startswith("/api/"):
                    raise ApiError("Nicht gefunden.", 404)
                self._json(self._post_api(path[5:], self._body(raw)))
            except ApiError as exc:
                self._error(str(exc), exc.status)
            except JobError as exc:
                self._error(str(exc), 400)
            except KeyError:
                self._error("Projekt nicht gefunden.", 404)
            except ValueError as exc:
                self._error(str(exc), 400)
            except Exception as exc:  # letzte Absicherung
                self._error(f"Interner Fehler: {exc}", 500)

        # -- GET-API -----------------------------------------------------------
        def _get_api(self, route: str, query: dict[str, list[str]]) -> Any:
            parts = [p for p in route.split("/") if p]
            if parts == ["status"]:
                return studio.status()
            if parts == ["settings"]:
                return studio.settings.public()
            if parts == ["projects"]:
                return {"projects": studio.store.list()}
            if parts == ["examples"]:
                return {"examples": list_examples()}
            if len(parts) == 2 and parts[0] == "projects":
                return studio.store.get(parts[1]).to_dict()
            if len(parts) == 4 and parts[0] == "projects" and parts[2] == "version":
                return {"code": studio.store.get(parts[1]).read_version(int(parts[3]))}
            if len(parts) == 2 and parts[0] == "jobs":
                job = studio.jobs.get(parts[1])
                if not job:
                    raise ApiError("Auftrag nicht gefunden.", 404)
                since = int((query.get("since") or ["0"])[0] or 0)
                return job.to_dict(since)
            raise ApiError("Nicht gefunden.", 404)

        # -- POST-API ----------------------------------------------------------
        def _post_api(self, route: str, body: dict[str, Any]) -> Any:
            parts = [p for p in route.split("/") if p]
            settings = studio.settings

            def start(kind: str, title: str, work: Callable, project=None) -> dict[str, Any]:
                if project is not None:
                    running = studio.running_job(project.id)
                    if running:
                        raise ApiError("Für dieses Projekt läuft bereits ein Vorgang – bitte warten "
                                       "oder abbrechen.", 409)
                job = studio.jobs.start(kind, title, work)
                if project is not None:
                    studio.project_jobs[project.id] = job.id
                return {"job": job.id}

            def project_for(body: dict[str, Any], kind: str, default_title: str):
                if body.get("project_id"):
                    return studio.store.get(str(body["project_id"]))
                title = str(body.get("title") or "").strip() or default_title
                return studio.store.create(title, kind)

            if parts == ["settings"]:
                settings.update(body)
                openscad.detect(settings.get("openscad_path") or "", refresh=True)
                return studio.status()
            if parts == ["models", "gemini"]:
                key = str(body.get("api_key") or "").strip() or settings.get("gemini_api_key")
                models = GeminiProvider(key, "").list_models()
                return {"models": [m["id"] for m in models if "image" not in m["id"]],
                        "image_models": [m["id"] for m in models if "image" in m["id"]]}
            if parts == ["openscad", "detect"]:
                openscad.detect(settings.get("openscad_path") or "", refresh=True)
                return studio.status()
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
                return {"cancelled": studio.jobs.cancel(parts[1])}

            if parts == ["ai", "generate"]:
                instruction = str(body.get("instruction") or "").strip()
                images = body.get("images") or []
                if not instruction and not images:
                    raise ApiError("Bitte beschreibe das Objekt oder lade ein Bild hoch.")
                decoded = [decode_data_url(i) for i in images[:6]]
                make_provider(settings)   # fehlt z. B. der API-Schlüssel → sofort melden
                project = project_for(body, "ai", _title_from(instruction) or "KI-Modell")
                names = [project.add_input(*d) for d in decoded]
                return {**start("ai", "KI-Konstruktion", lambda job: pipeline.ai_generate(
                    job, project, settings, instruction, names), project), "project_id": project.id}

            if parts == ["ai", "refine"]:
                project = studio.store.get(str(body.get("project_id") or ""))
                change = str(body.get("change") or "").strip()
                if not change:
                    raise ApiError("Bitte beschreibe die gewünschte Änderung.")
                make_provider(settings)
                names = [project.add_input(*decode_data_url(i)) for i in (body.get("images") or [])[:4]]
                return {**start("ai", "KI-Änderung", lambda job: pipeline.ai_refine(
                    job, project, settings, change, names), project), "project_id": project.id}

            if parts == ["render"]:
                code = body.get("code")
                if code is not None and not str(code).strip():
                    raise ApiError("Der Code ist leer.")
                project = project_for(body, "code", "Eigener Code")
                return {**start("render", "Rendern", lambda job: pipeline.render_code(
                    job, project, settings, code), project), "project_id": project.id}

            if parts == ["image", "convert"]:
                mode = str(body.get("mode") or "")
                params = body.get("params") or {}
                if not isinstance(params, dict):
                    raise ApiError("Ungültige Parameter.")
                decoded = decode_data_url(body["image"]) if body.get("image") else None
                project = project_for(body, "image", str(body.get("title") or "Bildmodell"))
                if decoded:
                    image_name = project.add_input(*decoded)
                else:
                    image_name = project.meta.get("source_image") or ""
                    if not image_name:
                        raise ApiError("Bitte ein Bild hochladen.")
                return {**start("image", "Bild → 3D", lambda job: pipeline.image_to_model(
                    job, project, settings, image_name, mode, params), project), "project_id": project.id}

            if parts == ["image", "preview"]:
                return _image_preview(body)

            if parts == ["image", "generate"]:
                prompt = str(body.get("prompt") or "").strip()
                preset = str(body.get("preset") or "frei")
                images = [decode_data_url(i)[0] for i in (body.get("images") or [])[:3]]
                if not prompt and not images:
                    raise ApiError("Bitte einen Bild-Prompt eingeben.")
                aspect = str(body.get("aspect") or "1:1")
                return start("imagegen", "Bild erzeugen", lambda job: pipeline.generate_image(
                    job, settings, prompt, preset, images, aspect))

            if len(parts) == 3 and parts[0] == "examples" and parts[2] == "open":
                example = next((e for e in list_examples() if e["id"] == parts[1]), None)
                if not example:
                    raise ApiError("Beispiel nicht gefunden.", 404)
                code = (EXAMPLES_DIR / f"{example['id']}.scad").read_text(encoding="utf-8")
                project = studio.store.create(example["title"], "code")
                project.write_code(code, "Beispiel")
                return {**start("render", "Beispiel rendern", lambda job: pipeline.render_code(
                    job, project, settings, None), project), "project_id": project.id}

            if parts == ["projects"]:
                project = studio.store.create(str(body.get("title") or "Neues Modell"), "code")
                if body.get("code"):
                    project.write_code(str(body["code"]), "Neu")
                return project.to_dict()

            if len(parts) == 3 and parts[0] == "projects":
                project = studio.store.get(parts[1])
                action = parts[2]
                if action == "delete":
                    if studio.running_job(project.id):
                        raise ApiError("Für dieses Projekt läuft noch ein Vorgang.", 409)
                    studio.store.delete(project.id)
                    return {"deleted": project.id}
                if action == "rename":
                    project.update(title=str(body.get("title") or project.meta["title"])[:120])
                    return project.to_dict()
                if action == "open":
                    if not project.scad_file.exists():
                        raise ApiError("Noch kein Code vorhanden.")
                    openscad.open_in_openscad(project.scad_file, pipeline.openscad_info(settings))
                    return {"ok": True}
                if action == "collisions":
                    return {**start("fit", "Passungsprüfung", lambda job: pipeline.collision_check(
                        job, project, settings), project), "project_id": project.id}
                if action == "folder":
                    openscad.open_folder(project.path)
                    return {"ok": True}
                if action == "restore":
                    code = project.read_version(int(body.get("version") or 0))
                    project.write_code(code, f"Wiederhergestellt: Version {body.get('version')}")
                    return project.to_dict()
            raise ApiError("Nicht gefunden.", 404)

        def _zip(self, project_id: str) -> None:
            project = studio.store.get(project_id)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(project.path.rglob("*")):
                    rel = path.relative_to(project.path).as_posix()
                    if path.is_file() and not rel.startswith(("versions/", "view_", "_passung")) \
                            and not rel.endswith(".tmp"):
                        zf.write(path, rel)
            name = project.id + ".zip"
            self._send(200, buf.getvalue(), "application/zip",
                       {"Content-Disposition": f'attachment; filename="{name}"'})

    return Handler


def list_examples() -> list[dict[str, str]]:
    """Mitgelieferte Beispiele: Titel und Beschreibung aus dem Kopfkommentar."""
    items = []
    for path in sorted(EXAMPLES_DIR.glob("*.scad")):
        if path.name == "studio.scad":
            continue
        head = path.read_text(encoding="utf-8")[:2000]
        lines = [line.strip().strip("=*/").strip() for line in head.splitlines()]
        start = next((i for i, line in enumerate(lines) if line), 0)
        title = lines[start] if lines else path.stem
        # Beschreibung: Zeilen nach dem Titel bis zur ersten Leerzeile
        desc_lines = []
        for line in lines[start + 1:]:
            if not line:
                break
            desc_lines.append(line)
        desc = " ".join(desc_lines)
        if len(desc) > 180:
            desc = desc[:177].rsplit(" ", 1)[0] + " …"
        items.append({"id": path.stem, "title": title[:80], "description": desc})
    return items


def _title_from(text: str) -> str:
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    title = " ".join(words[:6])
    return title[:60]


def _image_preview(body: dict[str, Any]) -> dict[str, Any]:
    """Schnelle Vorschau der Bildumwandlung (Maske bzw. Höhenkarte)."""
    from PIL import Image as PILImage, ImageDraw

    from . import imaging

    data, _ = decode_data_url(str(body.get("image") or ""))
    params = body.get("params") or {}
    mode = str(body.get("mode") or "extrude")
    img = imaging.load_image(data)
    if mode in pipeline.RELIEF_MODES:
        grid = imaging.heightmap(img, resolution=min(int(params.get("resolution", 200)), 300),
                                 invert=bool(params.get("invert", mode == "lithophane")),
                                 blur=float(params.get("blur", 0)), gamma=float(params.get("gamma", 1.0)),
                                 auto_contrast=bool(params.get("auto_contrast", True)))
        h, w = len(grid), len(grid[0])
        out = PILImage.new("L", (w, h))
        out.putdata([int(v * 255) for row in grid for v in row])
        out = out.resize((w * max(1, 400 // w), h * max(1, 400 // w)), PILImage.NEAREST)
        info = {"width": w, "height": h}
    else:
        mask = imaging.binary_mask(img, resolution=int(params.get("resolution", 400)),
                                   threshold=pipeline._opt_int(params.get("threshold")),
                                   invert=bool(params.get("invert", False)),
                                   use_alpha=str(params.get("use_alpha", "auto")),
                                   blur=float(params.get("blur", 0)), cleanup=int(params.get("cleanup", 1)))
        h, w = len(mask), len(mask[0])
        polygons = imaging.trace_contours(mask, simplify=float(params.get("simplify", 0.6)),
                                          smooth=int(params.get("smooth", 1)),
                                          min_area=float(params.get("min_area", 4)))
        scale = max(1, 500 // max(w, h))
        out = PILImage.new("RGB", (w * scale, h * scale), (245, 243, 238))
        fill = PILImage.new("L", (w, h))
        fill.putdata([200 if v else 0 for row in mask for v in row])
        fill = fill.resize(out.size, PILImage.NEAREST)
        out.paste((60, 60, 70), (0, 0), fill)
        draw = ImageDraw.Draw(out)
        for poly in polygons:
            pts = [(x * scale, (h - y) * scale) for x, y in poly]
            draw.line(pts + pts[:1], fill=(216, 170, 90), width=2)
        info = {"width": w, "height": h, "contours": len(polygons), "points": sum(len(p) for p in polygons)}
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return {"preview": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), **info}


class StudioHTTPServer(ThreadingHTTPServer):
    # Unter Windows erlaubt SO_REUSEADDR einem zweiten Programm denselben Port –
    # dann liefen zwei Studios auf 8765. Dort also abschalten.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def serve(host: str = "127.0.0.1", port: int = 8765, studio: Studio | None = None) -> ThreadingHTTPServer:
    studio = studio or Studio()
    handler = make_handler(studio)
    last_error: Exception | None = None
    for candidate in range(port, port + 20):
        try:
            httpd = StudioHTTPServer((host, candidate), handler)
            httpd.daemon_threads = True
            httpd.studio = studio  # type: ignore[attr-defined]
            return httpd
        except OSError as exc:  # Port belegt → nächsten probieren
            last_error = exc
    raise RuntimeError(f"Kein freier Port gefunden: {last_error}")
