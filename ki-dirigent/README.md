# KI-Dirigent

Eine lokale Arbeitsplattform für deinen PC. **Ollama ist der Dirigent:** Es nimmt jede Aufgabe an, plant sie lokal und
entscheidet, **welche Plattform** (Claude, Gemini, ChatGPT – oder Ollama selbst) **mit welchem Modell** sie bekommt.
Einfaches erledigt Ollama kostenlos selbst. Nur was wirklich Können braucht, geht weiter, und dann nur mit den Daten,
die dafür nötig sind.

```
            Du (Browser: http://localhost:8765)
                          │
                ┌─────────▼─────────┐
                │  Ollama – Dirigent │  plant · sucht auf dem PC · bereitet Daten vor
                └─────────┬─────────┘  verdichtet Verläufe · führt Ergebnisse zusammen
     ┌──────────┬─────────┼──────────┬──────────────┐
     ▼          ▼         ▼          ▼              ▼
  Ollama     Claude     Gemini     ChatGPT      (optional APIs)
  lokal    Claude Code  Gemini CLI  Codex CLI    Claude/Gemini/OpenAI
 kostenlos  dein Abo    dein Konto  dein Abo     per API-Schlüssel
     └──────────┴─────────┴──────────┴──────────────┘
                          ▼
          Gemeinsamer Arbeitsplatz-Ordner auf deinem PC
```

## Warum das Tokens spart

| Maßnahme | Wirkung |
|---|---|
| **Lokale Planung** | Ollama stuft jede Aufgabe ein (Kategorie + Schwierigkeit 1–3). Das kostet nichts. |
| **Günstigster passender Spezialist** | Strategie `sparsam`: Es gewinnt der billigste Spezialist, dessen Niveau reicht. Einfaches bleibt bei Ollama. |
| **Deine Programme zuerst** | Claude Code, Gemini CLI und Codex laufen über dein Abo. Die APIs (pro Token bezahlt) sind nur Reserve. |
| **Datenvorbereitung durch Ollama** | Große Dateien liest Ollama lokal und schickt nur die relevanten Stellen weiter (z. B. 52.000 → 1.100 Zeichen). |
| **Verlauf verdichten** | Ältere Gesprächsteile fasst Ollama zusammen. Nur die letzten Nachrichten gehen wörtlich mit. |
| **Zerlegen** | Gemischte Aufgaben werden geteilt: Den einfachen Teil macht Ollama, den schweren ein Profi. Ollama fügt die Teile zusammen. |
| **Cache** | Dieselbe Frage zweimal heißt: Die Antwort kommt aus dem Speicher, ohne neuen Aufruf. |
| **Knappe Anweisungen** | Kurze Systemtexte, keine Füllsätze. Leere KI-Ordner-Dateien werden gar nicht erst mitgeschickt. |
| **Stopp-Knopf** | Bricht laufende Programme sofort ab, damit kein Kontingent verloren geht. |

Im Reiter **Verbrauch** siehst du, wie viel Prozent lokal erledigt wurden.

## Installation (Windows, macOS, Linux)

1. **Python 3.10 oder neuer**: <https://www.python.org/downloads/> (unter Windows „Add to PATH“ anhaken).
   Weitere Pakete werden nicht gebraucht.
2. **Ollama**: <https://ollama.com>. Danach im Terminal:
   ```
   ollama pull qwen2.5:7b          # Dirigent + Allrounder (ca. 4,7 GB)
   ollama pull qwen2.5-coder:7b    # lokaler Programmierer
   ollama pull nomic-embed-text    # optional: Bedeutungssuche auf dem PC
   ```
   Auf schwächeren PCs nimmst du stattdessen `llama3.2:3b` und trägst es in `config.json` bei `router_modell`,
   `helfer_modell` und beim Spezialisten `lokal` ein.
3. **Deine KI-Programme**: Die Plattform steuert die **Kommandozeilen-Versionen**. Einmal im Terminal prüfen:
   ```
   claude --version     # Claude Code
   gemini --version     # Gemini CLI
   codex --version      # Codex (ChatGPT)
   ```
   Fehlt eines, installierst du es so (gleiches Konto, gleiches Abo wie die App):
   ```
   npm install -g @anthropic-ai/claude-code
   npm install -g @google/gemini-cli
   npm install -g @openai/codex
   ```
   Jedes Programm **einmal normal starten und anmelden** (`claude`, `gemini`, `codex`). Danach nutzt der Dirigent
   die Programme automatisch. Die Desktop-Apps (Claude Desktop, ChatGPT-App) lassen sich von außen nicht steuern.
4. **Optional: API-Schlüssel** als Reserve: `.env.example` nach `.env` kopieren und eintragen.
5. **Starten**: unter Windows Doppelklick auf `start.bat`, unter macOS/Linux `./start.sh`.
   Der Browser öffnet sich unter <http://localhost:8765>.

## Bedienung

- **Mitte**: Aufgabe eintippen. Über jeder Antwort zeigt eine Zeile den Weg, zum Beispiel
  `Ollama · qwen2.5:7b → Claude Sonnet (Claude Code) · code · Stufe 3`. Darunter stehen die verbrauchten Tokens
  (lokal/extern) und die gespeicherten Dateien.
- **Auswahl „Automatisch“**: Ollama entscheidet. Du kannst aber auch selbst einen Spezialisten wählen.
- **Strategie**:
  - `sparsam` ist der günstigste passende Spezialist (Standard).
  - `Ollamas Wahl` heißt: Ollamas Vorschlag gilt direkt.
  - `beste Qualität` nimmt den stärksten Spezialisten.
- **zerlegen / PC-Suche / speichern** schalten Zerlegung, Ollamas Suche auf dem PC und das automatische Speichern
  von Dateien an oder aus.
- **Arbeitsplatz** (rechts): der gemeinsame Ordner aller KIs. Du kannst Dateien öffnen, bearbeiten, hochladen und mit
  📎 an eine Aufgabe hängen. Wenn eine KI eine Datei erzeugt (` ```datei:pfad/name.ext `), wird sie hier
  gespeichert. Ältere Stände landen in `.versionen/`.
- **PC**: Hier durchsuchst du deinen ganzen PC.
  - 📎 hängt eine Datei oder einen ganzen Ordner an.
  - ⤓ **bereitstellen** kopiert sie in den Arbeitsplatz (`pc-import/`).
  - **Scannen** legt einen Suchindex an (nur Namen und Anfänge, schnell).
  - **Mit Ollama analysieren**: Ollama liest die Textdateien und beschreibt jede in einem Satz. Damit findet es
    später passende Ordner auch zu frei formulierten Aufgaben, zum Beispiel „fasse meine Steuerunterlagen 2025
    zusammen“.

## KI-Ordner (wie `~/.claude`)

Im Arbeitsplatz hat jede KI ihren eigenen Ordner:

```
arbeitsplatz/
  gemeinsam/ANWEISUNGEN.md   ← gilt für alle KIs
  claude/    ANWEISUNGEN.md  GEDAECHTNIS.md
  gemini/    ANWEISUNGEN.md  GEDAECHTNIS.md
  chatgpt/   ANWEISUNGEN.md  GEDAECHTNIS.md
  ollama/    ANWEISUNGEN.md  GEDAECHTNIS.md
```

- **ANWEISUNGEN.md** wird der jeweiligen KI bei jeder Aufgabe mitgegeben, zum Beispiel „Schreibe Code immer mit
  Kommentaren auf Deutsch“. Halte die Datei kurz, sie kostet bei jedem Aufruf Tokens.
- **GEDAECHTNIS.md**: Schreibt eine KI einen ` ```merken `-Block, landet der Inhalt hier und ist beim nächsten Mal
  bekannt.
- Die Programme lesen zusätzlich **ihre eigenen Ordner** (`~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md`,
  `~/.codex/AGENTS.md`). Der Reiter **KI-Ordner** zeigt, ob sie gefunden wurden. Wenn auch die API-Spezialisten diese
  Dateien bekommen sollen, setzt du in `config.json` den Wert `ki_ordner.tool_anweisungen_nutzen` auf `true`.

## PC-Zugriff und Sicherheit

- **Lesen**: Standardmäßig ist der ganze PC freigegeben (`"pc": {"lesen": ["*"]}`). Wenn du nur bestimmte Ordner
  freigeben willst, trägst du sie ein, z. B. `["~/Dokumente", "D:\\Projekte"]`.
- **Gesperrt** sind immer: SSH- und Cloud-Schlüssel, `.env`-Dateien, Zertifikate, Passwort-Datenbanken,
  Browser-Logins und Cookies (`pc.sperren`). Diese Dateien werden weder angezeigt, noch gescannt, noch an eine KI
  geschickt.
- **Schreiben** dürfen die KIs nur im Arbeitsplatz. Weitere Ordner gibst du gezielt in `pc.schreiben` frei.
- Cloud-KIs bekommen **nie direkten Zugriff** auf den PC. Ollama sucht lokal, und nur das vorbereitete Ergebnis
  geht hinaus.
- Der Server lauscht nur auf `127.0.0.1`. Anfragen von fremden Webseiten werden blockiert.

## Mit anderen Ollama-Oberflächen nutzen

Der Dirigent spricht auch die Ollama-Schnittstelle. In **Open WebUI** (oder jeder anderen Ollama-App mit einstellbarer
Adresse) trägst du als Ollama-URL `http://localhost:8765/ollama` ein. Dort erscheint dann das Modell **`dirigent`**,
das alles verteilt. Deine normalen Ollama-Modelle bleiben daneben nutzbar.

## Konfiguration (`config.json`)

| Bereich | Bedeutung |
|---|---|
| `ollama.router_modell` / `helfer_modell` | lokales Modell für Planung bzw. Vorbereitung und Zusammenfassen |
| `spezialisten[]` | alle KIs: `anbieter` (`ollama`/`cli`/`anthropic`/`openai`/`gemini`), `modell`, `kategorien`, `niveau` (1–3), `kosten` (0–5), `staerken` |
| `spezialisten[].aktiv` | `"auto"` bedeutet aktiv, sobald das Programm gefunden wird. `false` heißt aus. |
| `spezialisten[].befehl` | Startbefehl eines Programms. Ohne `{prompt}` wird die Aufgabe über die Standardeingabe übergeben. |
| `sparen.strategie` | `sparsam` / `ollama` / `qualitaet` |
| `sparen.vorbereiten_ab_zeichen` | ab wie vielen Zeichen Anhang Ollama vorher kürzt |
| `sparen.verlauf_letzte` | wie viele letzte Nachrichten wörtlich mitgehen |
| `pc.lesen` / `pc.schreiben` / `pc.sperren` | Freigaben und Sperrliste |

Die **Modellnamen** der Anbieter ändern sich oft. Im Reiter **Spezialisten → testen** siehst du, welche Modelle
dein Konto gerade anbietet. Den passenden Namen trägst du dann in `config.json` ein.

## Fehlerbehebung

- **„Ollama nicht erreichbar“**: Starte Ollama (App öffnen oder `ollama serve`).
- **Ein Programm erscheint als „nicht gefunden“**: Prüfe im Terminal `claude --version` usw. Wurde es gerade erst
  installiert, startest du den Dirigenten neu.
- **Die Gemini CLI oder Codex reagiert nicht**: Bei manchen Versionen musst du den `befehl` anpassen, z. B.
  `["gemini", "-m", "gemini-3-flash-preview", "-p", "{prompt}"]`. Unter Windows sind dann sehr lange Aufgaben
  begrenzt.
- **Claude Code rechnet über die API ab statt übers Abo**: Der Dirigent entfernt `ANTHROPIC_API_KEY` beim Start
  von Claude Code automatisch (`env_entfernen`), damit dein Abo genutzt wird.

## Tests

```
python -m unittest discover -s tests
```

Die Tests laufen ohne echte KI. Dafür gibt es Ersatz-Server und Ersatz-Programme.
