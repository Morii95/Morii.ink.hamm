"""Hintergrund-Aufträge mit Fortschrittsprotokoll.

KI-Aufrufe und OpenSCAD-Renderings dauern Sekunden bis Minuten. Die
Oberfläche startet deshalb einen Auftrag und fragt dessen Status ab.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable

from .openscad import Cancelled


class JobError(Exception):
    """Erwarteter Fehler mit verständlicher (deutscher) Meldung."""


class Job:
    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.title = title
        self.status = "running"          # running | done | error | cancelled
        self.log: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error = ""
        self.progress = 0.0
        self.created = time.time()
        self.finished: float | None = None
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()

    # Wird von den Arbeitsfunktionen aufgerufen
    def info(self, text: str, level: str = "info", **extra: Any) -> None:
        entry = {"t": round(time.time() - self.created, 1), "level": level, "text": text}
        entry.update(extra)
        with self._lock:
            self.log.append(entry)

    def warn(self, text: str, **extra: Any) -> None:
        self.info(text, "warning", **extra)

    def set_progress(self, value: float) -> None:
        self.progress = max(0.0, min(1.0, value))

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def to_dict(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            log = self.log[since:]
            count = len(self.log)
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "progress": round(self.progress, 3),
            "log": log,
            "log_count": count,
            "result": self.result,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - self.created, 1),
        }


class JobManager:
    def __init__(self, keep: int = 50):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep

    def start(self, kind: str, title: str, work: Callable[[Job], dict[str, Any] | None]) -> Job:
        job = Job(kind, title)
        with self._lock:
            self._jobs[job.id] = job
            self._prune()

        def runner() -> None:
            try:
                job.result = work(job) or {}
                job.status = "done"
                job.progress = 1.0
            except Cancelled:
                job.status = "cancelled"
                job.error = "Abgebrochen."
                job.info("Vorgang abgebrochen.", "warning")
            except JobError as exc:
                job.status = "error"
                job.error = str(exc)
                job.info(str(exc), "error")
            except Exception as exc:  # unerwartet: Details ins Protokoll
                job.status = "error"
                job.error = f"Unerwarteter Fehler: {exc}"
                job.info(job.error, "error", detail=traceback.format_exc())
            finally:
                job.finished = time.time()

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and job.status == "running":
            job.cancel_event.set()
            return True
        return False

    def _prune(self) -> None:
        finished = [j for j in self._jobs.values() if j.status != "running"]
        if len(self._jobs) <= self._keep:
            return
        for job in sorted(finished, key=lambda j: j.created)[: len(self._jobs) - self._keep]:
            self._jobs.pop(job.id, None)
