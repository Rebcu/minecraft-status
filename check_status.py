"""
Controlla UNA volta lo stato del server Minecraft e invia una notifica su Discord
(tramite webhook) quando cambia lo stato (online/offline) oppure il numero di
giocatori connessi. Pensato per girare su GitHub Actions ogni 5 minuti.

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
BLUE = 0x3498DB

# Aternos risponde al ping anche da spento, con un messaggio al posto del vero MOTD.
OFFLINE_WORDS = ("offline",)
STARTING_WORDS = ("starting", "loading", "preparing", "queue", "waiting", "saving", "stopping")


# ---------------------------------------------------------------- controllo
def classify(version, motd):
    text = f"{version} {motd}".lower()
    if any(w in text for w in STARTING_WORDS):
        return "starting"
    if any(w in text for w in OFFLINE_WORDS):
        return "offline"
    return "online"


def make_data(count, maximum, version, names):
    """Se i nomi non sono completi (non disponibili o lista troncata) li scarto."""
    if names is not None and len(names) != count:
        names = None
    return {"online": count, "max": maximum, "version": version, "names": names}


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
        sample = st.players.sample
        names = [p.name for p in sample] if sample else ([] if st.players.online == 0 else None)
        print(f"[debug] diretto: version={version!r} motd={motd!r} "
              f"players={st.players.online}/{st.players.max} names={names}")
        return classify(version, motd), make_data(
            st.players.online, st.players.max, version, names)
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
    count = players.get("online", 0)
    lst = players.get("list") or []
    names = [p.get("name_clean") for p in lst if p.get("name_clean")] if lst else ([] if count == 0 else None)
    print(f"[debug] API: version={version!r} motd={motd!r} players={count}/{players.get('max')} names={names}")
    return classify(version, motd), make_data(count, players.get("max", 0), version, names)


def get_status():
    """Restituisce (stato, dettagli). stato: online | offline | starting | unknown."""
    result = ping_direct() or ping_api()
    if result is None:
        return "unknown", {}
    return result


# ---------------------------------------------------------------- messaggi
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def embed_status(online, data):
    fields = [{"name": "Indirizzo", "value": f"`{SERVER_ADDRESS}`", "inline": False}]
    if online and data:
        fields.append({"name": "Giocatori", "value": f"{data['online']}/{data['max']}", "inline": True})
        if data.get("version"):
            fields.append({"name": "Versione", "value": data["version"], "inline": True})
        if data.get("names"):
            fields.append({"name": "Connessi", "value": ", ".join(data["names"]), "inline": False})
    return {
        "title": "🟢 Server ONLINE" if online else "🔴 Server OFFLINE",
        "description": "Il server è pronto, entra a giocare!" if online
                       else "Il server si è spento o non è raggiungibile.",
        "color": GREEN if online else RED,
        "fields": fields,
        "timestamp": now_iso(),
    }


def embed_players(prev, data):
    old, new = prev.get("players", 0), data["online"]
    diff = new - old
    old_names, new_names = prev.get("names"), data.get("names")

    lines = []
    if old_names is not None and new_names is not None:
        joined = sorted(set(new_names) - set(old_names))
        left = sorted(set(old_names) - set(new_names))
        if joined:
            lines.append("➕ Entrati: " + ", ".join(joined))
        if left:
            lines.append("➖ Usciti: " + ", ".join(left))
    if not lines:
        n = abs(diff)
        if diff > 0:
            lines.append("Un giocatore è entrato." if n == 1 else f"{n} giocatori sono entrati.")
        else:
            lines.append("Un giocatore è uscito." if n == 1 else f"{n} giocatori sono usciti.")

    fields = [{"name": "Giocatori", "value": f"{new}/{data['max']}", "inline": True}]
    if new_names:
        fields.append({"name": "Connessi", "value": ", ".join(new_names), "inline": False})

    return {
        "title": f"👥 Giocatori: {old} → {new}",
        "description": "\n".join(lines),
        "color": BLUE,
        "fields": fields,
        "footer": {"text": SERVER_ADDRESS},
        "timestamp": now_iso(),
    }


def send_webhook(embed):
    r = requests.post(
        WEBHOOK_URL,
        json={"username": "Minecraft Status", "embeds": [embed]},
        timeout=15,
    )
    r.raise_for_status()


# ---------------------------------------------------------------- stato salvato
def load_state():
    try:
        s = json.loads(STATE_FILE.read_text())
        return s if isinstance(s, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))


# ---------------------------------------------------------------- main
def main():
    if not WEBHOOK_URL:
        print("[!] Manca la variabile WEBHOOK_URL (Secret DISCORD_WEBHOOK_URL).")
        sys.exit(1)

    status, data = get_status()
    print(f"Stato attuale: {status}")

    if status == "unknown":
        print("Nessuna risposta da nessun metodo: stato sconosciuto, nessuna notifica.")
        return
    if status == "starting":
        print("Server in avvio: nessuna notifica, riprovo al prossimo giro.")
        return

    online = status == "online"
    prev = load_state()
    new_state = {
        "online": online,
        "players": data["online"] if online else 0,
        "names": data.get("names") if online else [],
    }

    embed = None
    if online != prev.get("online"):
        embed = embed_status(online, data)
    elif online and prev.get("players") is not None and data["online"] != prev["players"]:
        embed = embed_players(prev, data)

    if embed:
        try:
            send_webhook(embed)
        except requests.RequestException as e:
            print(f"[!] Errore invio webhook: {e}")
            sys.exit(1)  # non aggiorno lo stato: riprovo al prossimo giro
        print(f"[+] Notifica inviata: {embed['title']}")
    else:
        print("Nessun cambiamento da notificare.")

    if new_state != prev:
        save_state(new_state)


if __name__ == "__main__":
    main()
