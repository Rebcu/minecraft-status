"""
Controlla UNA volta lo stato del server Minecraft e, se è cambiato rispetto
all'ultimo controllo, invia una notifica su Discord tramite webhook.
Pensato per girare su GitHub Actions ogni 5 minuti.

Variabili d'ambiente:
  WEBHOOK_URL     (obbligatoria) URL del webhook Discord, dai Secrets di GitHub
  SERVER_ADDRESS  (facoltativa)  default: ClamsMC.aternos.me
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from mcstatus import JavaServer

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
SERVER_ADDRESS = os.environ.get("SERVER_ADDRESS", "ClamsMC.aternos.me")
STATE_FILE = Path("state.json")

GREEN = 0x2ECC71
RED = 0xE74C3C

# Aternos risponde al ping anche da spento, con un messaggio al posto del vero MOTD.
OFFLINE_WORDS = ("offline",)
STARTING_WORDS = ("starting", "loading", "preparing", "queue", "waiting", "saving", "stopping")


def get_status():
    """Restituisce (stato, dettagli). stato: 'online' | 'offline' | 'starting'."""
    try:
        st = JavaServer.lookup(SERVER_ADDRESS, timeout=10).status()
    except Exception as e:
        print(f"[debug] nessuna risposta dal server: {e}")
        return "offline", {}

    version = st.version.name or ""
    try:
        motd = st.motd.to_plain()
    except Exception:
        motd = str(st.description)

    print(f"[debug] version={version!r} motd={motd!r} players={st.players.online}/{st.players.max}")

    text = f"{version} {motd}".lower()
    if any(w in text for w in STARTING_WORDS):
        state = "starting"
    elif any(w in text for w in OFFLINE_WORDS):
        state = "offline"
    else:
        state = "online"

    return state, {"online": st.players.online, "max": st.players.max, "version": version}


def send_webhook(online, data):
    fields = [{"name": "Indirizzo", "value": f"`{SERVER_ADDRESS}`", "inline": False}]
    if online and data:
        fields.append({"name": "Giocatori", "value": f"{data['online']}/{data['max']}", "inline": True})
        if data.get("version"):
            fields.append({"name": "Versione", "value": data["version"], "inline": True})

    embed = {
        "title": "🟢 Server ONLINE" if online else "🔴 Server OFFLINE",
        "description": "Il server è pronto, entra a giocare!" if online
                       else "Il server si è spento o non è raggiungibile.",
        "color": GREEN if online else RED,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        WEBHOOK_URL,
        json={"username": "Minecraft Status", "embeds": [embed]},
        timeout=15,
    )
    r.raise_for_status()


def load_last_state():
    try:
        return json.loads(STATE_FILE.read_text())["online"]
    except (FileNotFoundError, KeyError, ValueError):
        return None


def main():
    if not WEBHOOK_URL:
        print("[!] Manca la variabile WEBHOOK_URL (Secret DISCORD_WEBHOOK_URL).")
        sys.exit(1)

    state, data = get_status()
    print(f"Stato attuale: {state}")

    if state == "starting":
        print("Server in avvio: nessuna notifica, riprovo al prossimo giro.")
        return

    online = state == "online"
    if online == load_last_state():
        print("Nessun cambiamento.")
        return

    try:
        send_webhook(online, data)
    except requests.RequestException as e:
        print(f"[!] Errore invio webhook: {e}")
        sys.exit(1)  # non aggiorno lo stato: riprovo al prossimo giro

    STATE_FILE.write_text(json.dumps({"online": online}))
    print(f"[+] Notifica inviata: {'ONLINE' if online else 'OFFLINE'}")


if __name__ == "__main__":
    main()
