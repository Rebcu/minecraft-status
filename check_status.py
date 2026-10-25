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
import time
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
 
 
def classify(version, motd):
    text = f"{version} {motd}".lower()
    if any(w in text for w in STARTING_WORDS):
        return "starting"
    if any(w in text for w in OFFLINE_WORDS):
        return "offline"
    return "online"
 
 
def ping_direct():
    """Ping diretto al server, 3 tentativi. None se non risponde."""
    for attempt in range(1, 4):
        try:
            st = JavaServer.lookup(SERVER_ADDRESS, timeout=20).status()
        except Exception as e:
            print(f"[debug] ping diretto, tentativo {attempt}/3 fallito: {e}")
            time.sleep(3)
            continue
 
        version = st.version.name or ""
        try:
            motd = st.motd.to_plain()
        except Exception:
            motd = str(st.description)
        print(f"[debug] diretto: version={version!r} motd={motd!r} "
              f"players={st.players.online}/{st.players.max}")
        return classify(version, motd), {
            "online": st.players.online, "max": st.players.max, "version": version}
    return None
 
 
def ping_api():
    """Fallback: servizio esterno mcstatus.io. None se non riesce."""
    try:
        r = requests.get(f"https://api.mcstatus.io/v2/status/java/{SERVER_ADDRESS}", timeout=20)
        j = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[debug] fallback API fallito: {e}")
        return None
 
    if not j.get("online"):
        print("[debug] fallback API: server non raggiungibile")
        return None
 
    version = (j.get("version") or {}).get("name_clean") or ""
    motd = (j.get("motd") or {}).get("clean") or ""
    players = j.get("players") or {}
    print(f"[debug] API: version={version!r} motd={motd!r} players={players}")
    return classify(version, motd), {
        "online": players.get("online", 0), "max": players.get("max", 0), "version": version}
 
 
def get_status():
    """Restituisce (stato, dettagli). stato: online | offline | starting | unknown."""
    result = ping_direct() or ping_api()
    if result is None:
        return "unknown", {}
    return result
 
 
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
 
    if state == "unknown":
        print("Nessuna risposta da nessun metodo: stato sconosciuto, nessuna notifica.")
        return
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
 
