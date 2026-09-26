"""Projektablage: jedes Modell bekommt einen eigenen Ordner.

<projekte>/<id>/
    meta.json        Titel, Verlauf, Einstellungen, Prüfergebnisse
    model.scad       aktueller OpenSCAD-Code (in OpenSCAD direkt öffnbar)
    studio.scad      Kopie der Verbindungs-Bibliothek (Gewinde, Stecker …)
    model.stl        gerendertes Gesamtmodell
    parts/*.stl      einzelne Druckteile
    inputs/*         Eingabebilder
    versions/*.scad  frühere Code-Stände
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

LIBRARY_DIR = Path(__file__).parent / "library"
LIBRARY_FILES = ("studio.scad",)

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]{1,120}$")


def slugify(text: str, fallback: str = "modell") -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:40].strip("-") or fallback


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Project:
    def __init__(self, path: Path):
        self.path = path
        self.id = path.name
        self._lock = threading.RLock()
        self.meta: dict[str, Any] = {}
        meta_file = path / "meta.json"
        if meta_file.exists():
            try:
                self.meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.meta = {}
        self.meta.setdefault("id", self.id)
        self.meta.setdefault("title", self.id)
        self.meta.setdefault("chat", [])
        self.meta.setdefault("versions", [])

    # -- Metadaten ----------------------------------------------------------
    def save(self) -> None:
        with self._lock:
            self.meta["updated"] = now_iso()
            tmp = self.path / "meta.json.tmp"
            tmp.write_text(json.dumps(self.meta, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path / "meta.json")

    def update(self, **values: Any) -> None:
        with self._lock:
            self.meta.update(values)
            self.save()

    def add_chat(self, role: str, text: str, images: list[str] | None = None, **extra: Any) -> None:
        with self._lock:
            entry = {"role": role, "text": text, "time": now_iso()}
            if images:
                entry["images"] = images
            entry.update(extra)
            self.meta["chat"].append(entry)
            self.save()

    # -- Dateien ------------------------------------------------------------
    def file(self, name: str) -> Path:
        """Sicherer Pfad innerhalb des Projektordners (kein ../)."""
        parts = [p for p in name.replace("\\", "/").split("/") if p]
        if not parts or any(p in (".", "..") or not _NAME_RE.match(p) for p in parts):
            raise ValueError("Ungültiger Dateiname.")
        path = self.path.joinpath(*parts).resolve()
        if self.path.resolve() not in path.parents:
            raise ValueError("Ungültiger Dateiname.")
        return path

    @property
    def scad_file(self) -> Path:
        return self.path / "model.scad"

    @property
    def stl_file(self) -> Path:
        return self.path / "model.stl"

    def read_code(self) -> str:
        return self.scad_file.read_text(encoding="utf-8") if self.scad_file.exists() else ""

    def write_code(self, code: str, note: str = "") -> int:
        """Speichert den Code als aktuellen Stand und als neue Version."""
        with self._lock:
            code = code.replace("\r\n", "\n")
            if not code.endswith("\n"):
                code += "\n"
            if code == self.read_code() and self.meta["versions"]:
                return self.meta["versions"][-1]["n"]
            self.install_library()
            self.scad_file.write_text(code, encoding="utf-8")
            versions = self.path / "versions"
            versions.mkdir(exist_ok=True)
            n = (self.meta["versions"][-1]["n"] + 1) if self.meta["versions"] else 1
            (versions / f"v{n:03d}.scad").write_text(code, encoding="utf-8")
            self.meta["versions"].append({"n": n, "time": now_iso(), "note": note[:200]})
            self.save()
            return n

    def read_version(self, n: int) -> str:
        path = self.path / "versions" / f"v{int(n):03d}.scad"
        if not path.exists():
            raise ValueError("Version nicht gefunden.")
        return path.read_text(encoding="utf-8")

    def install_library(self) -> None:
        for name in LIBRARY_FILES:
            src = LIBRARY_DIR / name
            if src.exists():
                dst = self.path / name
                if not dst.exists() or dst.read_bytes() != src.read_bytes():
                    shutil.copyfile(src, dst)

    def add_input(self, data: bytes, ext: str) -> str:
        with self._lock:
            inputs = self.path / "inputs"
            inputs.mkdir(exist_ok=True)
            ext = ext.lower().lstrip(".")
            if ext not in ("png", "jpg", "jpeg", "webp", "gif", "bmp"):
                ext = "png"
            n = len(list(inputs.glob("*"))) + 1
            name = f"inputs/bild_{n}.{ext}"
            self.file(name).write_bytes(data)
            return name

    def summary(self) -> dict[str, Any]:
        thumb = "preview.png" if (self.path / "preview.png").exists() else ""
        return {
            "id": self.id,
            "title": self.meta.get("title", self.id),
            "kind": self.meta.get("kind", ""),
            "created": self.meta.get("created", ""),
            "updated": self.meta.get("updated", ""),
            "thumb": thumb,
            "has_stl": self.stl_file.exists(),
        }

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.meta)
        data["code"] = self.read_code()
        data["has_stl"] = self.stl_file.exists()
        data["folder"] = str(self.path)
        return data


class ProjectStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, Project] = {}

    def create(self, title: str, kind: str, **meta: Any) -> Project:
        with self._lock:
            base = time.strftime("%Y%m%d-%H%M%S") + "-" + slugify(title)
            name, n = base, 2
            while (self.root / name).exists():
                name, n = f"{base}-{n}", n + 1
            path = self.root / name
            path.mkdir(parents=True)
            project = Project(path)
            project.meta.update({"title": title.strip()[:120] or "Neues Modell", "kind": kind,
                                 "created": now_iso()})
            project.meta.update(meta)
            project.install_library()
            project.save()
            self._cache[project.id] = project
            return project

    def get(self, project_id: str) -> Project:
        if not project_id or not _ID_RE.match(project_id):
            raise KeyError(project_id)
        with self._lock:
            cached = self._cache.get(project_id)
            if cached and cached.path.exists():
                return cached
            path = self.root / project_id
            if not (path / "meta.json").exists():
                raise KeyError(project_id)
            project = Project(path)
            self._cache[project_id] = project
            return project

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in self.root.iterdir():
            if (path / "meta.json").exists():
                try:
                    items.append(self.get(path.name).summary())
                except KeyError:
                    continue
        items.sort(key=lambda p: p.get("updated") or p.get("created") or "", reverse=True)
        return items

    def delete(self, project_id: str) -> None:
        project = self.get(project_id)
        with self._lock:
            self._cache.pop(project_id, None)
        shutil.rmtree(project.path, ignore_errors=True)
