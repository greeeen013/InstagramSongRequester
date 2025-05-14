import os
import re
import time
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from instagrapi import Client
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# Načtení .env proměnných
load_dotenv()

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
GROUP_THREAD_ID = os.getenv("GROUP_THREAD_ID")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")

DEFAULT_COOLDOWN = 60  # minut
BOT_ACTIVE = True
last_message_id = None

# Inicializace databáze
conn = sqlite3.connect('cooldown.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS cooldowns (user TEXT PRIMARY KEY, last_time TEXT)''')
conn.commit()

# Instagram přihlášení
cl = Client()
print("[DEBUG] Přihlašuji se k Instagramu...")
cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

# Spotify přihlášení
print("[DEBUG] Přihlašuji se k Spotify...")
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-modify-playback-state user-read-playback-state"
))

# Načtení threadu a mapování uživatelů
try:
    thread = cl.direct_thread(GROUP_THREAD_ID)
    user_map = {u.pk: u.username for u in thread.users}
    print("[DEBUG] Načteno", len(user_map), "uživatelů ve vlákně.")
except Exception as e:
    print(f"⛔ Chyba při načítání threadu: {e}")
    user_map = {}

# Pomocné funkce
def extract_spotify_link(text):
    match = re.search(r'(https?://open\.spotify\.com/track/\S+)', text)
    return match.group(1) if match else None

def can_post(user):
    c.execute("SELECT last_time FROM cooldowns WHERE user=?", (user,))
    row = c.fetchone()
    if row is None:
        print(f"[DEBUG] Uživatel {user} přidává poprvé.")
        return True
    last_time = datetime.fromisoformat(row[0])
    elapsed = datetime.now() - last_time
    print(f"[DEBUG] Uživatel {user} čeká {elapsed.total_seconds() / 60:.1f} minut.")
    return elapsed >= timedelta(minutes=DEFAULT_COOLDOWN)

def update_post_time(user):
    print(f"[DEBUG] Aktualizuji čas poslední zprávy pro {user}")
    c.execute("REPLACE INTO cooldowns (user, last_time) VALUES (?, ?)", (user, datetime.now().isoformat()))
    conn.commit()

def add_to_queue(link):
    track_id = link.split("/")[-1].split("?")[0]
    track_uri = f"spotify:track:{track_id}"
    print(f"[DEBUG] Přidávám do fronty: {track_uri}")
    sp.add_to_queue(track_uri)

def handle_admin_command(msg, username):
    global BOT_ACTIVE, DEFAULT_COOLDOWN
    text = msg.text.lower()
    print(f"[DEBUG] Zpracovávám admin příkaz: {text}")

    if "stop" in text:
        BOT_ACTIVE = False
        cl.direct_send("⏸️ Bot pozastaven.", [msg.user_id])
    elif "start" in text:
        BOT_ACTIVE = True
        cl.direct_send("▶️ Bot znovu spuštěn.", [msg.user_id])
    elif "set cooldown" in text:
        try:
            value = int(re.findall(r"\d+", text)[0])
            DEFAULT_COOLDOWN = value
            cl.direct_send(f"🕒 Cooldown změněn na {value} minut.", [msg.user_id])
        except:
            cl.direct_send("❌ Neplatný formát cooldownu.", [msg.user_id])

# Hlavní smyčka
print("🚀 Bot je aktivní a naslouchá...")

while True:
    try:
        messages = cl.direct_messages(thread_id=GROUP_THREAD_ID, amount=1)
        if not messages:
            time.sleep(5)
            continue

        msg = messages[0]

        # Kontrola, zda je zpráva nová
        if msg.id == last_message_id:
            print("[DEBUG] Nepřišla žádná nová zpráva.")
            time.sleep(5)
            continue

        last_message_id = msg.id  # Uložit ID zprávy

        user_id = msg.user_id
        username = user_map.get(user_id, str(user_id))
        text = msg.text or ""
        print(f"[DEBUG] Nová zpráva od {username}: {text}")

        # Admin příkazy
        if username == ADMIN_USERNAME and any(cmd in text.lower() for cmd in ["start", "stop", "set cooldown"]):
            handle_admin_command(msg, username)
            continue

        if not BOT_ACTIVE:
            print("[DEBUG] Bot je pozastaven.")
            continue

        link = extract_spotify_link(text)
        if not link:
            print("[DEBUG] Zpráva neobsahuje Spotify odkaz.")
            continue

        if can_post(username):
            try:
                add_to_queue(link)
                update_post_time(username)
                cl.direct_like_message(msg.id)
                print("[DEBUG] ✅ Skladba úspěšně přidána.")
            except Exception as e:
                print(f"❌ Chyba při přidávání do fronty: {e}")
                cl.direct_send("❌ Chyba při přidávání do fronty.", [user_id])
        else:
            cl.direct_send("🕒 Cooldown ještě nevypršel.", [user_id])

    except Exception as e:
        print(f"⛔ Globální chyba: {e}")

    time.sleep(5)
