"""KI-Anbieter: Google Gemini, Anthropic Claude, OpenAI-kompatibel (z. B.
Ollama lokal) und das lokal installierte Claude Code (claude -p).

Alle Anbieter haben dieselbe Schnittstelle:
    provider.complete(system, messages, cancel=...) -> str
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..jobs import JobError
from ..openscad import Cancelled, kill_tree, popen_group

HTTP_TIMEOUT = 600


@dataclass
class Image:
    data: bytes
    mime: str = "image/png"
    label: str = ""

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass
class Message:
    role: str                      # "user" | "assistant"
    text: str
    images: list[Image] = field(default_factory=list)
    raw: Any = None                # Originalantwort des Anbieters (z. B. Gemini-Signaturen)


def guess_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def _http_json(url: str, body: dict, headers: dict[str, str], cancel: threading.Event | None,
               timeout: float = HTTP_TIMEOUT) -> dict:
    """POST mit JSON; läuft in einem Thread, damit "Abbrechen" sofort greift."""
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                box["status"] = resp.status
                box["body"] = resp.read()
        except urllib.error.HTTPError as exc:
            box["status"] = exc.code
            box["body"] = exc.read()
        except Exception as exc:  # Netzwerkfehler
            box["exc"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while thread.is_alive():
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        thread.join(0.2)
    if "exc" in box:
        raise JobError(f"Keine Verbindung zum KI-Dienst: {box['exc']}")
    raw = box.get("body") or b""
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {"_raw": raw[:500].decode("utf-8", "replace")}
    data["_status"] = box.get("status", 0)
    return data


class Provider:
    name = "?"

    def __init__(self, model: str):
        self.model = model

    def label(self) -> str:
        return f"{self.name} ({self.model})" if self.model else self.name

    def complete(self, system: str, messages: list[Message], *,
                 cancel: threading.Event | None = None, max_tokens: int = 32000) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Google Gemini (REST)
# ---------------------------------------------------------------------------

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _gemini_error(data: dict) -> str:
    status = data.get("_status", 0)
    err = data.get("error") or {}
    message = err.get("message") or data.get("_raw") or ""
    reason = json.dumps(err.get("details", ""))
    if status in (400, 401, 403) and ("API_KEY" in reason or "API key" in message):
        return "Gemini: API-Schlüssel ungültig. Bitte in den Einstellungen prüfen (aistudio.google.com)."
    if status == 429:
        return ("Gemini: Kontingent erschöpft oder zu viele Anfragen (429). Kurz warten, ein "
                "kleineres Modell wählen oder das Abrechnungskonto in Google AI Studio prüfen.")
    if status == 404:
        return f"Gemini: Modell nicht gefunden – bitte Modellnamen in den Einstellungen prüfen. ({message})"
    return f"Gemini-Fehler {status}: {message}"


def _gemini_contents(messages: list[Message]) -> list[dict]:
    contents = []
    for msg in messages:
        if msg.role == "assistant" and isinstance(msg.raw, dict) and msg.raw.get("parts"):
            # Unverändert zurückschicken: Gemini 3 erwartet die thoughtSignature
            contents.append({"role": "model", "parts": msg.raw["parts"]})
            continue
        parts: list[dict] = []
        for img in msg.images:
            if img.label:
                parts.append({"text": img.label})
            parts.append({"inline_data": {"mime_type": img.mime, "data": img.b64}})
        parts.append({"text": msg.text})
        contents.append({"role": "model" if msg.role == "assistant" else "user", "parts": parts})
    return contents


class GeminiProvider(Provider):
    name = "Gemini"
    last_raw: Any = None

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        if not api_key:
            raise JobError("Kein Gemini-API-Schlüssel hinterlegt. Kostenlos erhältlich unter "
                           "https://aistudio.google.com/apikey – dann in den Einstellungen eintragen.")
        self.api_key = api_key

    def _post(self, model: str, body: dict, cancel: threading.Event | None) -> dict:
        url = f"{GEMINI_BASE}/{model}:generateContent"
        for attempt in range(3):
            data = _http_json(url, body, {"x-goog-api-key": self.api_key}, cancel)
            if data["_status"] in (500, 502, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))  # überlastet: kurz erneut versuchen
                continue
            break
        if data["_status"] != 200:
            raise JobError(_gemini_error(data))
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise JobError(f"Gemini hat die Anfrage blockiert ({feedback['blockReason']}).")
        return data

    def complete(self, system, messages, *, cancel=None, max_tokens=32000):
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": _gemini_contents(messages),
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens},
        }
        data = self._post(self.model, body, cancel)
        candidates = data.get("candidates") or []
        if not candidates:
            raise JobError("Gemini hat keine Antwort geliefert.")
        cand = candidates[0]
        self.last_raw = cand.get("content")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        reason = cand.get("finishReason", "")
        if not text.strip():
            raise JobError(f"Gemini hat keinen Text geliefert (finishReason: {reason or '?'}).")
        return text

    def list_models(self, cancel: threading.Event | None = None) -> list[dict[str, str]]:
        """Verfügbare Modelle live abfragen (Name + Anzeigename)."""
        req = urllib.request.Request(f"{GEMINI_BASE}?pageSize=1000",
                                     headers={"x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except ValueError:
                body = {}
            body["_status"] = exc.code
            raise JobError(_gemini_error(body)) from None
        except OSError as exc:
            raise JobError(f"Keine Verbindung zu Gemini: {exc}") from None
        models = []
        for m in data.get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            models.append({"id": m.get("name", "").split("/", 1)[-1],
                           "label": m.get("displayName", "")})
        return models

    def generate_image(self, prompt: str, images: list[Image], model: str,
                       aspect_ratio: str = "1:1", cancel: threading.Event | None = None) -> tuple[bytes, str]:
        parts: list[dict] = [{"inline_data": {"mime_type": i.mime, "data": i.b64}} for i in images]
        parts.append({"text": prompt})
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": aspect_ratio}},
        }
        data = self._post(model, body, cancel)
        notes = []
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inline_data") or part.get("inlineData")
                if inline and inline.get("data") and not part.get("thought"):
                    return base64.b64decode(inline["data"]), part.get("text", "")
                if part.get("text") and not part.get("thought"):
                    notes.append(part["text"])
            if cand.get("finishReason") not in (None, "STOP"):
                notes.append(f"finishReason: {cand.get('finishReason')}")
        raise JobError("Gemini hat kein Bild erzeugt. " + " ".join(notes)[:400])


# ---------------------------------------------------------------------------
# Anthropic Claude (offizielles SDK)
# ---------------------------------------------------------------------------

# Modelle, die serverseitige Ausweichmodelle bei Ablehnungen unterstützen
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1")


class AnthropicProvider(Provider):
    name = "Claude"

    def __init__(self, api_key: str, model: str):
        super().__init__(model)
        if not api_key:
            raise JobError("Kein Anthropic-API-Schlüssel hinterlegt (platform.claude.com). "
                           "Alternativ den Anbieter „Claude Code (lokal)“ wählen.")
        try:
            import anthropic  # noqa: F401  (optional installiert)
        except ImportError:
            raise JobError("Für Claude bitte einmal „pip install anthropic“ ausführen "
                           "(oder die Startdatei erneut starten).") from None
        self.api_key = api_key

    def complete(self, system, messages, *, cancel=None, max_tokens=32000):
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, max_retries=2)
        api_messages = []
        for msg in messages:
            content: list[dict] = []
            for img in msg.images:
                if img.label:
                    content.append({"type": "text", "text": img.label})
                content.append({"type": "image", "source": {"type": "base64",
                                                            "media_type": img.mime, "data": img.b64}})
            content.append({"type": "text", "text": msg.text})
            api_messages.append({"role": msg.role, "content": content})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max(max_tokens, 64000),
            "system": system,
            "messages": api_messages,
        }
        if not self.model.startswith("claude-haiku"):
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": "high"}
        betas = []
        if self.model in _FALLBACK_MODELS:
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"

        try:
            stream_api = client.beta.messages if betas else client.messages
            if betas:
                kwargs["betas"] = betas
            with stream_api.stream(**kwargs) as stream:
                for _ in stream:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                final = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise JobError("Claude: API-Schlüssel ungültig.") from None
        except anthropic.PermissionDeniedError as exc:
            raise JobError(f"Claude: keine Berechtigung ({exc.message}).") from None
        except anthropic.NotFoundError:
            raise JobError(f"Claude: Modell „{self.model}“ nicht gefunden.") from None
        except anthropic.RateLimitError:
            raise JobError("Claude: Zu viele Anfragen oder Kontingent erschöpft – bitte kurz warten.") from None
        except anthropic.BadRequestError as exc:
            raise JobError(f"Claude: ungültige Anfrage ({exc.message}).") from None
        except anthropic.APIStatusError as exc:
            raise JobError(f"Claude-Fehler {exc.status_code}: {exc.message}") from None
        except anthropic.APIConnectionError:
            raise JobError("Claude: keine Verbindung – Internetverbindung prüfen.") from None

        if final.stop_reason == "refusal":
            raise JobError("Claude hat die Anfrage abgelehnt.")
        text = "".join(b.text for b in final.content if b.type == "text")
        if not text.strip():
            raise JobError("Claude hat keinen Text geliefert.")
        return text


# ---------------------------------------------------------------------------
# OpenAI-kompatibel (OpenAI, OpenRouter, LM Studio, Ollama …)
# ---------------------------------------------------------------------------

class OpenAICompatProvider(Provider):
    name = "OpenAI-kompatibel"

    def __init__(self, base_url: str, api_key: str, model: str):
        super().__init__(model)
        if not base_url:
            raise JobError("Bitte eine Basis-URL angeben (z. B. http://localhost:11434/v1 für Ollama).")
        if not model:
            raise JobError("Bitte einen Modellnamen angeben (z. B. qwen2.5vl für Ollama).")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def complete(self, system, messages, *, cancel=None, max_tokens=32000):
        api_messages: list[dict] = [{"role": "system", "content": system}]
        for msg in messages:
            if msg.images:
                content: list[dict] = []
                for img in msg.images:
                    if img.label:
                        content.append({"type": "text", "text": img.label})
                    content.append({"type": "image_url",
                                    "image_url": {"url": f"data:{img.mime};base64,{img.b64}"}})
                content.append({"type": "text", "text": msg.text})
                api_messages.append({"role": msg.role, "content": content})
            else:
                api_messages.append({"role": msg.role, "content": msg.text})
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"model": self.model, "messages": api_messages, "temperature": 0.3,
                "max_tokens": min(max_tokens, 16000)}
        data = _http_json(self.base_url + "/chat/completions", body, headers, cancel)
        if data["_status"] == 400 and any(k in json.dumps(data.get("error", "")) for k in
                                         ("max_tokens", "temperature", "max_completion_tokens")):
            # z. B. OpenAI-Reasoning-Modelle: nur Standardwerte erlaubt
            body.pop("temperature", None)
            body["max_completion_tokens"] = body.pop("max_tokens")
            data = _http_json(self.base_url + "/chat/completions", body, headers, cancel)
        status = data["_status"]
        if status != 200:
            err = data.get("error")
            message = err.get("message") if isinstance(err, dict) else (err or data.get("_raw", ""))
            if status == 401:
                raise JobError("API-Schlüssel ungültig (401).")
            if status == 404:
                raise JobError(f"Modell oder URL nicht gefunden (404): {message}")
            raise JobError(f"Fehler {status}: {message}")
        choices = data.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        if not text.strip():
            raise JobError("Das Modell hat keinen Text geliefert.")
        return text


# ---------------------------------------------------------------------------
# Claude Code (lokal installiert, „claude -p“)
# ---------------------------------------------------------------------------

def find_claude_cli(custom: str = "") -> str | None:
    if custom:
        return custom if os.path.isfile(custom) else None
    for name in ("claude", "claude.exe", "claude.cmd"):
        found = shutil.which(name)
        if found:
            return found
    home = Path.home()
    candidates = [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude",
                  Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        candidates = [home / ".local" / "bin" / "claude.exe", appdata / "npm" / "claude.cmd",
                      appdata / "npm" / "claude.exe"]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def parse_claude_stream(output: str) -> tuple[str, dict[str, Any]]:
    """Wertet `claude -p --output-format stream-json` aus.

    Liefert (gesamter Antworttext, result-Ereignis). Alle Textblöcke aller
    Assistenten-Nachrichten werden in Reihenfolge verbunden – so bleibt eine
    Antwort vollständig, die über das Ausgabelimit hinaus fortgesetzt wurde.
    """
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    result: dict[str, Any] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            for block in message.get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    key = (str(message.get("id", "")), block["text"])
                    if key not in seen:
                        seen.add(key)
                        parts.append(block["text"])
        elif event.get("type") == "result":
            result = event
    text = "".join(parts) if parts else str(result.get("result") or "")
    return text, result


class ClaudeCodeProvider(Provider):
    """Nutzt das lokal installierte Claude Code im Druckmodus.

    Bilder werden als Dateien übergeben und von Claude Code mit dem
    Read-Werkzeug angesehen. Alle anderen Werkzeuge sind gesperrt.
    """

    name = "Claude Code"

    def __init__(self, path: str, model: str, effort: str = "medium"):
        super().__init__(model)
        self.effort = effort if effort in ("low", "medium", "high", "xhigh", "max") else ""
        exe = find_claude_cli(path)
        if not exe:
            raise JobError("Claude Code wurde nicht gefunden. Installation: https://claude.com/claude-code "
                           "– danach einmal „claude“ im Terminal starten und anmelden.")
        self.exe = exe

    def label(self) -> str:
        return f"Claude Code ({self.model or 'Standardmodell'})"

    def complete(self, system, messages, *, cancel=None, max_tokens=32000):
        workdir = Path(tempfile.mkdtemp(prefix="scadstudio-claude-"))
        try:
            lines = [system, "", "=" * 60, ""]
            n = 0
            for msg in messages:
                speaker = "AUFTRAG" if msg.role == "user" else "DEINE FRÜHERE ANTWORT"
                lines.append(f"### {speaker}")
                for img in msg.images:
                    n += 1
                    ext = {"image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(img.mime, "png")
                    path = workdir / f"bild_{n}.{ext}"
                    path.write_bytes(img.data)
                    label = img.label or f"Bild {n}"
                    lines.append(f"{label}: Datei {path} – bitte mit dem Read-Werkzeug ansehen.")
                lines.append(msg.text)
                lines.append("")
            lines.append("Antworte jetzt direkt mit deiner Antwort (keine Dateien schreiben, "
                         "keine Befehle ausführen).")
            prompt = "\n".join(lines)
            # stream-json liefert jede Teilantwort einzeln. Wichtig, wenn Claude Code das
            # Ausgabelimit erreicht und in einer zweiten Nachricht weiterschreibt – das
            # einfache json-Format enthält dann nur den letzten Teil.
            cmd = [self.exe, "-p", "--output-format", "stream-json", "--verbose",
                   "--allowedTools", "Read",
                   "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch",
                   "--max-turns", "12", "--add-dir", str(workdir)]
            if self.model:
                cmd += ["--model", self.model]
            if self.effort:   # ohne Begrenzung denkt Claude Code bei Konstruktionen sehr lange
                cmd += ["--effort", self.effort]
            env = dict(os.environ)
            env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
            proc = popen_group(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=str(workdir), env=env)
            box: dict[str, Any] = {}

            def talk() -> None:
                box["out"], box["err"] = proc.communicate(prompt.encode("utf-8"))

            thread = threading.Thread(target=talk, daemon=True)
            thread.start()
            start = time.monotonic()
            while thread.is_alive():
                if (cancel is not None and cancel.is_set()) or time.monotonic() - start > 1800:
                    kill_tree(proc)
                    thread.join(5)
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    raise JobError("Claude Code hat nicht rechtzeitig geantwortet (30 min).")
                thread.join(0.3)
            out = (box.get("out") or b"").decode("utf-8", "replace")
            err = (box.get("err") or b"").decode("utf-8", "replace")
            text, result = parse_claude_stream(out)
            if proc.returncode != 0 and self.effort and "--effort" in err and "unknown" in err.lower():
                # ältere Claude-Code-Version ohne --effort → ohne erneut versuchen
                self.effort = ""
                return self.complete(system, messages, cancel=cancel, max_tokens=max_tokens)
            if proc.returncode != 0 or result.get("is_error") or not text.strip():
                detail = str(result.get("error") or result.get("subtype") or err.strip()
                             or out.strip())[-400:]
                if "login" in detail.lower() or "auth" in detail.lower():
                    raise JobError("Claude Code ist nicht angemeldet – bitte einmal „claude“ im "
                                   "Terminal starten und anmelden.")
                raise JobError(f"Claude Code-Fehler: {detail or 'keine Antwort'}")
            return text
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------

PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "anthropic": "Anthropic Claude (API)",
    "openai": "OpenAI-kompatibel / Ollama",
    "claude_cli": "Claude Code (lokal)",
}


def make_provider(settings: Any, provider: str | None = None, model: str | None = None) -> Provider:
    provider = provider or settings.get("provider")
    if provider == "gemini":
        return GeminiProvider(settings.get("gemini_api_key"), model or settings.get("gemini_model"))
    if provider == "anthropic":
        return AnthropicProvider(settings.get("anthropic_api_key"), model or settings.get("anthropic_model"))
    if provider == "openai":
        return OpenAICompatProvider(settings.get("openai_base_url"), settings.get("openai_api_key"),
                                    model or settings.get("openai_model"))
    if provider == "claude_cli":
        return ClaudeCodeProvider(settings.get("claude_cli_path"), model or settings.get("claude_cli_model"),
                                  settings.get("claude_cli_effort") or "medium")
    raise JobError(f"Unbekannter KI-Anbieter: {provider}")


def provider_status(settings: Any) -> dict[str, Any]:
    """Welche Anbieter sind eingerichtet? (für die Oberfläche)"""
    return {
        "gemini": bool(settings.get("gemini_api_key")),
        "anthropic": bool(settings.get("anthropic_api_key")),
        "openai": bool(settings.get("openai_base_url") and settings.get("openai_model")),
        "claude_cli": bool(find_claude_cli(settings.get("claude_cli_path") or "")),
        "active": settings.get("provider"),
        "python": sys.version.split()[0],
    }
