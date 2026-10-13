"""Einheitliche Anbindung aller KI-Quellen.

- ollama    : lokale Modelle (kostenlos)
- cli       : installierte Programme (Claude Code, Gemini CLI, Codex) – laufen über dein Abo
- anthropic : Claude-API     (optional, API-Schlüssel)
- openai    : ChatGPT-API    (optional, API-Schlüssel)
- gemini    : Gemini-API     (optional, API-Schlüssel)

Nur Python-Standardbibliothek.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import api_schluessel


class AnbieterFehler(Exception):
    pass


@dataclass
class Antwort:
    text: str
    eingabe_tokens: int = 0
    ausgabe_tokens: int = 0
    anbieter: str = ""
    modell: str = ""
    geschaetzt: bool = False
    roh: dict = field(default_factory=dict)


# ---------------------------------------------------------------- HTTP-Helfer

def _anfrage(url, body=None, headers=None, timeout=600, methode=None):
    daten = json.dumps(body).encode("utf-8") if body is not None else None
    kopf = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=daten, headers=kopf, method=methode or ("POST" if daten else "GET"))
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise AnbieterFehler(f"HTTP {e.code} von {url.split('?')[0]}: {detail}") from None
    except urllib.error.URLError as e:
        raise AnbieterFehler(f"Keine Verbindung zu {url.split('?')[0]}: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise AnbieterFehler(f"Verbindungsfehler zu {url.split('?')[0]}: {e}") from None


def _sse_daten(resp):
    """Liefert die JSON-Objekte aus 'data:'-Zeilen eines Server-Sent-Events-Streams."""
    for roh in resp:
        zeile = roh.decode("utf-8", "replace").strip()
        if not zeile.startswith("data:"):
            continue
        inhalt = zeile[5:].strip()
        if not inhalt or inhalt == "[DONE]":
            continue
        try:
            yield json.loads(inhalt)
        except json.JSONDecodeError:
            continue


def schaetze_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def normalisiere(nachrichten):
    """Nur user/assistant, abwechselnd, beginnend mit user (für alle Anbieter gültig)."""
    ergebnis = []
    for n in nachrichten:
        rolle = "assistant" if n.get("role") == "assistant" else "user"
        inhalt = str(n.get("content") or "").strip()
        if not inhalt:
            continue
        if ergebnis and ergebnis[-1]["role"] == rolle:
            ergebnis[-1]["content"] += "\n\n" + inhalt
        else:
            ergebnis.append({"role": rolle, "content": inhalt})
    while ergebnis and ergebnis[0]["role"] != "user":
        ergebnis.pop(0)
    return ergebnis


def _teil_praefix(text: str, tag: str) -> int:
    """Länge des Endes von `text`, das ein angefangenes `tag` sein könnte."""
    for k in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:k]):
            return k
    return 0


class _DenkFilter:
    """Blendet <think>…</think>-Abschnitte lokaler Denkmodelle aus (auch im Stream)."""

    def __init__(self):
        self.puffer = ""
        self.drin = False

    def __call__(self, stueck: str) -> str:
        self.puffer += stueck
        raus = []
        while True:
            if self.drin:
                ende = self.puffer.find("</think>")
                if ende < 0:
                    self.puffer = self.puffer[-7:]
                    break
                self.puffer = self.puffer[ende + 8:]
                self.drin = False
            else:
                start = self.puffer.find("<think>")
                if start >= 0:
                    raus.append(self.puffer[:start])
                    self.puffer = self.puffer[start + 7:]
                    self.drin = True
                    continue
                k = _teil_praefix(self.puffer, "<think>")
                raus.append(self.puffer[:len(self.puffer) - k])
                self.puffer = self.puffer[len(self.puffer) - k:]
                break
        return "".join(raus)

    def rest(self) -> str:
        r = "" if self.drin else self.puffer
        self.puffer = ""
        return r


# ---------------------------------------------------------------- Ollama

def ollama_modelle(cfg, timeout=4) -> list[str]:
    resp = _anfrage(cfg["ollama"]["url"].rstrip("/") + "/api/tags", timeout=timeout)
    return [m.get("name", "") for m in json.load(resp).get("models", [])]


def ollama_hat(modell: str, installiert: list[str]) -> bool:
    return modell in installiert or (":" not in modell and f"{modell}:latest" in installiert)


def ollama_embed(cfg, modell, texte, timeout=120) -> list[list[float]]:
    url = cfg["ollama"]["url"].rstrip("/") + "/api/embed"
    resp = _anfrage(url, {"model": modell, "input": texte, "keep_alive": cfg["ollama"].get("keep_alive", "30m")},
                    timeout=timeout)
    return json.load(resp).get("embeddings", [])


def _ollama(cfg, modell, system, nachrichten, max_tokens, extra, on_delta, json_schema, timeout):
    o = cfg["ollama"]
    body = {
        "model": modell,
        "messages": ([{"role": "system", "content": system}] if system else []) + nachrichten,
        "stream": bool(on_delta),
        "options": {"num_ctx": o.get("num_ctx", 8192), "num_predict": max_tokens, **extra.get("options", {})},
        "keep_alive": o.get("keep_alive", "30m"),
    }
    if json_schema:
        body["format"] = json_schema
        body["options"].setdefault("temperature", 0)
    for k in ("think",):
        if k in extra:
            body[k] = extra[k]
    resp = _anfrage(o["url"].rstrip("/") + "/api/chat", body, timeout=timeout)
    filt = _DenkFilter()
    if not on_delta:
        obj = json.load(resp)
        if obj.get("error"):
            raise AnbieterFehler(f"Ollama: {obj['error']}")
        text = filt(obj.get("message", {}).get("content", "")) + filt.rest()
        return Antwort(text.strip(), obj.get("prompt_eval_count", 0), obj.get("eval_count", 0), "ollama", modell)
    text, ein, aus = "", 0, 0
    with resp:
        for roh in resp:
            if not roh.strip():
                continue
            obj = json.loads(roh)
            if obj.get("error"):
                raise AnbieterFehler(f"Ollama: {obj['error']}")
            stueck = filt(obj.get("message", {}).get("content", ""))
            if obj.get("done"):
                stueck += filt.rest()
                ein, aus = obj.get("prompt_eval_count", 0), obj.get("eval_count", 0)
            if stueck:
                text += stueck
                on_delta(stueck)
    return Antwort(text.strip(), ein, aus, "ollama", modell)


# ---------------------------------------------------------------- Anthropic (Claude API)

def _anthropic(cfg, modell, system, nachrichten, max_tokens, extra, on_delta, json_schema, timeout):
    schluessel = api_schluessel("anthropic")
    if not schluessel:
        raise AnbieterFehler("ANTHROPIC_API_KEY fehlt in .env")
    url = cfg.get("anthropic", {}).get("base_url", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    kopf = {"x-api-key": schluessel, "anthropic-version": "2023-06-01"}
    body = {"model": modell, "max_tokens": max_tokens, "messages": nachrichten, **extra}
    if system:
        body["system"] = system
    if on_delta:
        body["stream"] = True
    resp = _anfrage(url, body, kopf, timeout)
    if not on_delta:
        obj = json.load(resp)
        text = "".join(b.get("text", "") for b in obj.get("content", []) if b.get("type") == "text")
        u = obj.get("usage", {})
        ein = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        return Antwort(text, ein, u.get("output_tokens", 0), "anthropic", modell)
    text, ein, aus = "", 0, 0
    with resp:
        for ev in _sse_daten(resp):
            typ = ev.get("type")
            if typ == "message_start":
                u = ev.get("message", {}).get("usage", {})
                ein = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            elif typ == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                stueck = ev["delta"].get("text", "")
                text += stueck
                on_delta(stueck)
            elif typ == "message_delta":
                aus = ev.get("usage", {}).get("output_tokens", aus)
            elif typ == "error":
                raise AnbieterFehler(f"Claude: {ev.get('error', {}).get('message', ev)}")
    return Antwort(text, ein, aus, "anthropic", modell)


# ---------------------------------------------------------------- OpenAI (ChatGPT API)

def _openai(cfg, modell, system, nachrichten, max_tokens, extra, on_delta, json_schema, timeout):
    schluessel = api_schluessel("openai")
    if not schluessel:
        raise AnbieterFehler("OPENAI_API_KEY fehlt in .env")
    url = cfg.get("openai", {}).get("base_url", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    msgs = ([{"role": "system", "content": system}] if system else []) + nachrichten
    body = {"model": modell, "messages": msgs, "max_completion_tokens": max_tokens, **extra}
    if on_delta:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    resp = _anfrage(url, body, {"Authorization": f"Bearer {schluessel}"}, timeout)
    if not on_delta:
        obj = json.load(resp)
        text = (obj.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        u = obj.get("usage") or {}
        return Antwort(text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), "openai", modell)
    text, ein, aus = "", 0, 0
    with resp:
        for ev in _sse_daten(resp):
            if ev.get("error"):
                raise AnbieterFehler(f"ChatGPT: {ev['error']}")
            for wahl in ev.get("choices") or []:
                stueck = (wahl.get("delta") or {}).get("content") or ""
                if stueck:
                    text += stueck
                    on_delta(stueck)
            if ev.get("usage"):
                ein = ev["usage"].get("prompt_tokens", 0)
                aus = ev["usage"].get("completion_tokens", 0)
    return Antwort(text, ein, aus, "openai", modell)


# ---------------------------------------------------------------- Gemini API

def _gemini_text(obj):
    kand = (obj.get("candidates") or [{}])[0]
    teile = (kand.get("content") or {}).get("parts") or []
    return "".join(t.get("text", "") for t in teile if not t.get("thought"))


def _gemini_nutzung(obj):
    u = obj.get("usageMetadata") or {}
    return u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)


def _gemini(cfg, modell, system, nachrichten, max_tokens, extra, on_delta, json_schema, timeout):
    schluessel = api_schluessel("gemini")
    if not schluessel:
        raise AnbieterFehler("GEMINI_API_KEY fehlt in .env")
    basis = cfg.get("gemini", {}).get("base_url", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    aktion = "streamGenerateContent?alt=sse" if on_delta else "generateContent"
    url = f"{basis}/models/{modell}:{aktion}"
    inhalte = [{"role": "model" if n["role"] == "assistant" else "user", "parts": [{"text": n["content"]}]}
               for n in nachrichten]
    gen = {"maxOutputTokens": max_tokens, **extra.get("generationConfig", {})}
    body = {"contents": inhalte, "generationConfig": gen,
            **{k: v for k, v in extra.items() if k != "generationConfig"}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    resp = _anfrage(url, body, {"x-goog-api-key": schluessel}, timeout)
    if not on_delta:
        obj = json.load(resp)
        if not obj.get("candidates"):
            raise AnbieterFehler(f"Gemini: keine Antwort ({obj.get('promptFeedback', obj)})")
        ein, aus = _gemini_nutzung(obj)
        return Antwort(_gemini_text(obj), ein, aus, "gemini", modell)
    text, ein, aus = "", 0, 0
    with resp:
        for ev in _sse_daten(resp):
            if ev.get("error"):
                raise AnbieterFehler(f"Gemini: {ev['error']}")
            stueck = _gemini_text(ev)
            if stueck:
                text += stueck
                on_delta(stueck)
            if ev.get("usageMetadata"):
                ein, aus = _gemini_nutzung(ev)
    return Antwort(text, ein, aus, "gemini", modell)


# ---------------------------------------------------------------- Installierte Programme (CLI)

def cli_pfad(befehl: list[str]) -> str | None:
    return shutil.which(befehl[0]) if befehl else None


def _feld(obj, pfad: str):
    for teil in pfad.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(teil)
    return obj


def _cli(cfg, spez, system, nachrichten, on_delta, timeout, arbeitsordner, abbruch):
    befehl = list(spez.get("befehl") or [])
    programm = cli_pfad(befehl)
    if not programm:
        raise AnbieterFehler(f"Programm '{befehl[0] if befehl else '?'}' nicht gefunden (PATH)")

    # Alles in einen Prompt packen: CLI-Programme kennen keinen separaten Verlauf.
    teile = []
    if system:
        teile.append(f"[Anweisungen]\n{system}")
    if len(nachrichten) > 1:
        verlauf = "\n\n".join(f"{'Nutzer' if n['role'] == 'user' else 'Assistent'}: {n['content']}"
                              for n in nachrichten[:-1])
        teile.append(f"[Bisheriger Verlauf]\n{verlauf}")
    teile.append(f"[Aufgabe]\n{nachrichten[-1]['content'] if nachrichten else ''}")
    prompt = "\n\n".join(teile)

    argumente = [programm] + befehl[1:]
    per_stdin = not any("{prompt}" in a for a in argumente)
    argumente = [a.replace("{prompt}", prompt) for a in argumente]

    umgebung = dict(os.environ)
    for name in spez.get("env_entfernen", []):
        umgebung.pop(name, None)  # z. B. API-Schlüssel entfernen, damit das Abo genutzt wird

    ausgabe = spez.get("ausgabe") or {"format": "text"}
    streamen = on_delta and ausgabe.get("format") == "text"
    proc = subprocess.Popen(
        argumente, cwd=str(arbeitsordner), env=umgebung,
        stdin=subprocess.PIPE if per_stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    fehler_puffer = []
    t_err = threading.Thread(target=lambda: fehler_puffer.append(proc.stderr.read()), daemon=True)
    t_err.start()
    timer = threading.Timer(timeout, proc.kill)
    timer.start()

    def waechter():  # bricht das Programm ab, sobald der Nutzer stoppt (spart Kontingent)
        while proc.poll() is None:
            if abbruch is not None and abbruch.wait(0.5):
                proc.kill()
                return
            if abbruch is None:
                return
    threading.Thread(target=waechter, daemon=True).start()
    try:
        if per_stdin:
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except BrokenPipeError:
                pass
        text = ""
        for zeile in proc.stdout:
            if abbruch and abbruch.is_set():
                proc.kill()
                raise AnbieterFehler("abgebrochen")
            text += zeile
            if streamen:
                on_delta(zeile)
        proc.wait()
    except BaseException:
        proc.kill()
        raise
    finally:
        timer.cancel()
    t_err.join(timeout=2)
    stderr = "".join(fehler_puffer).strip()
    if proc.returncode != 0:
        raise AnbieterFehler(f"{befehl[0]} beendet mit Code {proc.returncode}: {(stderr or text)[-600:]}")

    ein = aus = 0
    geschaetzt = True
    if ausgabe.get("format") == "json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            raise AnbieterFehler(f"{befehl[0]}: unerwartete Ausgabe: {text[:300]}") from None
        if obj.get("is_error"):
            raise AnbieterFehler(f"{befehl[0]}: {obj.get('result') or obj}")
        text = str(_feld(obj, ausgabe.get("text", "result")) or "")
        ein = int(_feld(obj, ausgabe.get("ein", "")) or 0) if ausgabe.get("ein") else 0
        aus = int(_feld(obj, ausgabe.get("aus", "")) or 0) if ausgabe.get("aus") else 0
        geschaetzt = not (ein or aus)
        if on_delta and text:
            on_delta(text)
    if geschaetzt:
        ein, aus = schaetze_tokens(prompt), schaetze_tokens(text)
    return Antwort(text.strip(), ein, aus, "cli", spez.get("modell") or befehl[0], geschaetzt=geschaetzt)


# ---------------------------------------------------------------- Einstieg

def chat(cfg, spez, system, nachrichten, on_delta=None, json_schema=None, timeout=None,
         arbeitsordner=None, abbruch=None) -> Antwort:
    """Ruft den Spezialisten `spez` (dict aus config) auf."""
    anbieter = spez["anbieter"]
    nachrichten = normalisiere(nachrichten)
    if not nachrichten:
        raise AnbieterFehler("leere Anfrage")
    timeout = timeout or spez.get("timeout", 600)
    if anbieter == "cli":
        return _cli(cfg, spez, system, nachrichten, on_delta, timeout, arbeitsordner or ".", abbruch)
    funktion = {"ollama": _ollama, "anthropic": _anthropic, "openai": _openai, "gemini": _gemini}.get(anbieter)
    if not funktion:
        raise AnbieterFehler(f"unbekannter Anbieter '{anbieter}'")
    return funktion(cfg, spez["modell"], system, nachrichten, spez.get("max_tokens", 4096),
                    spez.get("extra") or {}, on_delta, json_schema, timeout)


def modelle_auflisten(cfg, anbieter: str) -> list[str]:
    """Für 'Verbindung testen' in der Oberfläche."""
    if anbieter == "ollama":
        return ollama_modelle(cfg)
    if anbieter == "anthropic":
        k = api_schluessel("anthropic")
        if not k:
            raise AnbieterFehler("ANTHROPIC_API_KEY fehlt")
        url = cfg.get("anthropic", {}).get("base_url", "https://api.anthropic.com").rstrip("/") + "/v1/models?limit=100"
        obj = json.load(_anfrage(url, headers={"x-api-key": k, "anthropic-version": "2023-06-01"}, timeout=15))
        return [m["id"] for m in obj.get("data", [])]
    if anbieter == "openai":
        k = api_schluessel("openai")
        if not k:
            raise AnbieterFehler("OPENAI_API_KEY fehlt")
        url = cfg.get("openai", {}).get("base_url", "https://api.openai.com/v1").rstrip("/") + "/models"
        obj = json.load(_anfrage(url, headers={"Authorization": f"Bearer {k}"}, timeout=15))
        return sorted(m["id"] for m in obj.get("data", []))
    if anbieter == "gemini":
        k = api_schluessel("gemini")
        if not k:
            raise AnbieterFehler("GEMINI_API_KEY fehlt")
        basis = cfg.get("gemini", {}).get("base_url", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        obj = json.load(_anfrage(f"{basis}/models?pageSize=200", headers={"x-goog-api-key": k}, timeout=15))
        return [m["name"].removeprefix("models/") for m in obj.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]
    if anbieter == "cli":
        gefunden = []
        for name in ("claude", "gemini", "codex", "ollama"):
            pfad = shutil.which(name)
            if pfad:
                gefunden.append(f"{name}: {pfad}")
        return gefunden
    raise AnbieterFehler(f"unbekannter Anbieter '{anbieter}'")


def entferne_denken(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
