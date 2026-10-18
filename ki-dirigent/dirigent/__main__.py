"""Start: python -m dirigent  [--port 8765] [--host 127.0.0.1] [--config pfad] [--kein-browser]"""
import argparse
import sys
import threading
import time
import webbrowser

from .config import lade_config
from .dirigent import Dirigent
from .server import starte


def main():
    ap = argparse.ArgumentParser(prog="dirigent", description="KI-Dirigent – Ollama verteilt Aufgaben an Spezialisten")
    ap.add_argument("--config", help="Pfad zur config.json")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--kein-browser", action="store_true", help="Browser nicht automatisch öffnen")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)  # Meldungen sofort anzeigen
    except (AttributeError, ValueError):
        pass

    cfg = lade_config(args.config)
    dirigent = Dirigent(cfg)
    server = starte(dirigent, args.host, args.port)
    host, port = server.server_address[:2]
    adresse = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0', '::') else host}:{port}"

    prog = dirigent.programme
    fehlen = prog.fehlende(dirigent.benoetigte_programme())
    if fehlen and cfg.get("programme", {}).get("automatisch_suchen", True):
        # gründlich einmal am Tag, sonst kurz (neu installierte Programme werden so trotzdem gefunden)
        gruendlich = time.time() - prog.letzte_suche > 86400
        print(f"\n  Suche Programme ({', '.join(fehlen)}) – höchstens {25 if gruendlich else 6} Sekunden …")
        prog.suchen(zeitlimit=25 if gruendlich else 6, tiefe=4 if gruendlich else 2)
        for name, pfad in prog.gefunden.items():
            print(f"    gefunden: {name:7} {pfad}")
    if not prog.ollama_erreichbar():
        print("  Ollama läuft nicht – starte es …")
        gestartet, meldung = prog.ollama_starten()
        print(f"    {meldung}")
    ok, modelle, fehler = dirigent.ollama_status(neu=True)
    print(f"\n  KI-Dirigent läuft:   {adresse}")
    print(f"  Arbeitsplatz:        {dirigent.arbeitsplatz.wurzel}")
    print(f"  Ollama:              {'verbunden, ' + str(len(modelle)) + ' Modelle' if ok else 'NICHT erreichbar – ' + fehler}")
    if ok:
        print(f"  Ollama-Modelle:      {', '.join(modelle) or 'keine – z. B. ollama pull qwen2.5:7b'}")
        router = dirigent.router_spez()
        print(f"  Dirigent plant mit:  {router['modell'] if router else '–'}")
    bereit = [s["name"] for s in dirigent.verfuegbar()]
    print(f"  Bereite Spezialisten: {', '.join(bereit) if bereit else 'keine'}")
    fehlen = prog.fehlende(dirigent.benoetigte_programme())
    if fehlen:
        print(f"  Nicht gefunden:      {', '.join(fehlen)}  → einrichten.bat installiert sie nach "
              f"{prog.installationsordner()}")
        if prog.apps:
            print(f"  Nur als Desktop-App: {', '.join(prog.apps)} – Desktop-Apps lassen sich nicht steuern, "
                  f"dafür braucht es die Kommandozeilen-Version")
    print(f"  Für Open WebUI & Co: {adresse}/ollama  (Modell 'dirigent')")
    print("  Beenden mit Strg+C\n")

    if not args.kein_browser:
        threading.Timer(0.8, lambda: webbrowser.open(adresse)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Beendet.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
