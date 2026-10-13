"""Ollama entscheidet: Kategorie, Schwierigkeit, Spezialist, evtl. Zerlegung und PC-Suche.

Die eigentliche Auswahl wird anschließend durch Regeln abgesichert (Strategie):
  sparsam   – günstigster Spezialist, der Kategorie und Schwierigkeit schafft (Ollama-Vorschlag bei Gleichstand)
  ollama    – Ollamas Wahl gilt, solange sie gültig ist
  qualitaet – stärkster passender Spezialist
"""
import json
import re

KATEGORIEN = ["chat", "text", "code", "analyse", "kreativ", "uebersetzung", "recherche", "mathe", "langer_kontext"]

_CODE = re.compile(r"```|\bdef |\bclass |function\s*\(|=>|\bimport |traceback|exception|fehlermeldung|\bbug|"
                   r"programmier|\bcode|skript|script|python|javascript|typescript|html|css|sql|regex|excel-formel", re.I)
_KREATIV = re.compile(r"gedicht|geschichte|slogan|werbe|instagram|tiktok|post\b|kreativ|ideen|brainstorm", re.I)
_UEBERSETZ = re.compile(r"übersetz|uebersetz|translate|auf englisch|ins englische|auf deutsch", re.I)


def heuristik(text: str, kontext_zeichen: int = 0) -> tuple[str, int]:
    """Notfall-Einschätzung ohne Ollama."""
    if kontext_zeichen > 100_000:
        return "langer_kontext", 2
    if _CODE.search(text):
        return "code", 2
    if _UEBERSETZ.search(text):
        return "uebersetzung", 1
    if _KREATIV.search(text):
        return "kreativ", 2
    if len(text) < 120:
        return "chat", 1
    return "text", 2


def schema(ids: list[str], max_teile: int, mit_pc: bool) -> dict:
    teil = {
        "type": "object",
        "properties": {
            "kategorie": {"type": "string", "enum": KATEGORIEN},
            "schwierigkeit": {"type": "integer", "enum": [1, 2, 3]},
            "spezialist": {"type": "string", "enum": ids},
            "auftrag": {"type": "string"},
        },
        "required": ["kategorie", "schwierigkeit", "spezialist", "auftrag"],
    }
    props = {"begruendung": {"type": "string"}}
    if mit_pc:
        props["pc_suche"] = {"type": "string"}
    props["teilaufgaben"] = {"type": "array", "items": teil, "minItems": 1, "maxItems": max_teile}
    return {"type": "object", "properties": props, "required": list(props)}


def system_prompt(verfuegbar: list[dict], max_teile: int, mit_pc: bool) -> str:
    zeilen = "\n".join(f"{s['id']} | {s['kosten']} | {s['niveau']} | {s.get('staerken', '')}" for s in verfuegbar)
    teile = [
        "Du bist der Dirigent einer KI-Plattform. Du löst Aufgaben nicht selbst, sondern planst, wer sie erledigt.",
        "Ziel: gute Ergebnisse mit möglichst wenig bezahlten Tokens.",
        "",
        "Kategorien: " + ", ".join(KATEGORIEN),
        "Schwierigkeit: 1 = einfach (Smalltalk, kurze Fakten, Umformulieren), 2 = normal (übliche Texte und "
        "Programmieraufgaben), 3 = schwer (komplexe Logik, Architektur, schwierige Fehlersuche, tiefe Analyse).",
        "",
        "Spezialisten (id | Kosten 0-5 | Niveau 1-3 | Stärken):",
        zeilen,
        "",
        "Regeln:",
        "- Wähle den günstigsten Spezialisten, dessen Niveau mindestens der Schwierigkeit entspricht und der zur Kategorie passt.",
        "- Einfaches bleibt lokal (Kosten 0).",
    ]
    if max_teile > 1:
        teile.append(f"- Normalerweise genau 1 Teilaufgabe. Nur zerlegen (max. {max_teile}), wenn die Anfrage aus klar "
                     "getrennten, unabhängigen Teilen besteht; jeder auftrag muss dann für sich verständlich sein.")
    else:
        teile.append("- Genau 1 Teilaufgabe.")
    teile.append("- auftrag: die Aufgabe in einem Satz.")
    if mit_pc:
        teile.append("- pc_suche: Wenn Dateien oder Ordner vom PC gebraucht werden, die nicht angehängt sind, nenne "
                     "Suchbegriffe (Datei-/Ordnernamen, Themen). Sonst leer lassen.")
    teile.append("- begruendung: höchstens ein kurzer Satz.")
    teile.append("Antworte nur mit JSON.")
    return "\n".join(teile)


def nutzer_text(nachricht: str, anhaenge: list[tuple[str, int]], vorher: str) -> str:
    teile = []
    if anhaenge:
        teile.append("Anhänge: " + ", ".join(f"{n} ({z:,} Zeichen)".replace(",", ".") for n, z in anhaenge[:20]))
    if vorher:
        teile.append("Vorherige Antwort (Auszug): " + vorher[:500])
    gekuerzt = nachricht if len(nachricht) <= 3000 else nachricht[:2500] + " … " + nachricht[-400:]
    teile.append("Anfrage:\n" + gekuerzt)
    return "\n\n".join(teile)


def waehle(kategorie: str, schwierigkeit: int, vorschlag: str | None, verfuegbar: list[dict],
           strategie: str = "sparsam", kontext_zeichen: int = 0, ausschliessen=()) -> dict | None:
    """Deterministische, nachvollziehbare Auswahl auf Basis von Ollamas Einschätzung."""
    rest = [s for s in verfuegbar if s["id"] not in ausschliessen]
    pool = [s for s in rest if s["max_kontext_zeichen"] >= kontext_zeichen]
    if not pool:
        pool = sorted(rest, key=lambda s: -s["max_kontext_zeichen"])[:1]
    if not pool:
        return None
    if strategie == "ollama":
        for s in pool:
            if s["id"] == vorschlag:
                return s
    fach = [s for s in pool if "alles" in s["kategorien"] or kategorie in s["kategorien"]] or pool
    if strategie == "qualitaet":
        return max(fach, key=lambda s: (s["niveau"], s["kosten"]))
    faehig = [s for s in fach if s["niveau"] >= schwierigkeit]
    if not faehig:
        return max(fach, key=lambda s: (s["niveau"], -s["kosten"]))
    return min(faehig, key=lambda s: (s["kosten"], 0 if s["id"] == vorschlag else 1, -s["niveau"]))


def _bereinige(roh: dict, ids: list[str], max_teile: int, nachricht: str) -> dict:
    teile = []
    for t in (roh.get("teilaufgaben") or [])[:max_teile]:
        if not isinstance(t, dict):
            continue
        kat = t.get("kategorie") if t.get("kategorie") in KATEGORIEN else "text"
        try:
            diff = min(3, max(1, int(t.get("schwierigkeit", 2))))
        except (TypeError, ValueError):
            diff = 2
        teile.append({
            "kategorie": kat,
            "schwierigkeit": diff,
            "vorschlag": t.get("spezialist") if t.get("spezialist") in ids else None,
            "auftrag": str(t.get("auftrag") or "").strip() or nachricht[:300],
        })
    if not teile:
        raise ValueError("keine Teilaufgaben im Plan")
    return {
        "begruendung": str(roh.get("begruendung") or "").strip()[:300],
        "pc_suche": str(roh.get("pc_suche") or "").strip()[:200],
        "teilaufgaben": teile,
    }


def plane(chat_fn, router_spez: dict | None, verfuegbar: list[dict], nachricht: str,
          anhaenge: list[tuple[str, int]], vorher: str, max_teile: int, mit_pc: bool, kontext_zeichen: int) -> dict:
    """Fragt das lokale Router-Modell. Bei Problemen greift die Heuristik."""
    ids = [s["id"] for s in verfuegbar]
    if router_spez and ids:
        try:
            antwort = chat_fn(router_spez, system_prompt(verfuegbar, max_teile, mit_pc),
                              [{"role": "user", "content": nutzer_text(nachricht, anhaenge, vorher)}],
                              json_schema=schema(ids, max_teile, mit_pc))
            plan = _bereinige(json.loads(antwort.text), ids, max_teile, nachricht)
            plan.update(router=router_spez["modell"], fehler=None,
                        tokens=(antwort.eingabe_tokens, antwort.ausgabe_tokens))
            return plan
        except Exception as e:  # noqa: BLE001 – jede Störung → Heuristik
            fehler = f"Router: {e}"
    else:
        fehler = "Kein lokales Router-Modell verfügbar – Regeln entscheiden."
    kat, diff = heuristik(nachricht, kontext_zeichen)
    return {"begruendung": "Einschätzung per Regeln", "pc_suche": "", "router": None, "fehler": fehler,
            "tokens": (0, 0),
            "teilaufgaben": [{"kategorie": kat, "schwierigkeit": diff, "vorschlag": None, "auftrag": nachricht[:300]}]}
