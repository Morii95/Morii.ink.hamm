"""Start: python -m dirigent  [--port 8765] [--host 127.0.0.1] [--config pfad] [--kein-browser]"""
import argparse
import threading
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

    cfg = lade_config(args.config)
    dirigent = Dirigent(cfg)
    server = starte(dirigent, args.host, args.port)
    host, port = server.server_address[:2]
    adresse = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0', '::') else host}:{port}"

    ok, modelle, fehler = dirigent.ollama_status(neu=True)
    print(f"\n  KI-Dirigent läuft:   {adresse}")
    print(f"  Arbeitsplatz:        {dirigent.arbeitsplatz.wurzel}")
    print(f"  Ollama:              {'verbunden, ' + str(len(modelle)) + ' Modelle' if ok else 'NICHT erreichbar – ' + fehler}")
    bereit = [s["name"] for s in dirigent.verfuegbar()]
    print(f"  Bereite Spezialisten: {', '.join(bereit) if bereit else 'keine'}")
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
