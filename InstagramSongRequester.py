import os
import re
import time
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from instagrapi import Client
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
import json

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

# Připojení k databázi
conn = sqlite3.connect('cooldown.db')
c = conn.cursor()

# Tabulka pro cooldown
c.execute('''CREATE TABLE IF NOT EXISTS cooldowns (
    user TEXT PRIMARY KEY,
    last_time TEXT
)''')

# Tabulka pro stav (poslední zpracované message ID)
c.execute('''CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
)''')
conn.commit()

# Pomocné SQLite funkce
def get_last_message_id():
    c.execute("SELECT value FROM state WHERE key = 'last_message_id'")
    row = c.fetchone()
    return row[0] if row else None

def set_last_message_id(message_id):
    c.execute("REPLACE INTO state (key, value) VALUES (?, ?)", ('last_message_id', message_id))
    conn.commit()

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
    print(f"[DEBUG] Aktualizuji cooldown pro {user}")
    c.execute("REPLACE INTO cooldowns (user, last_time) VALUES (?, ?)", (user, datetime.now().isoformat()))
    conn.commit()

def extract_spotify_link(text):
    match = re.search(r'(https?://open\.spotify\.com/track/\S+)', text)
    return match.group(1) if match else None

def add_to_queue(link):
    track_id = link.split("/")[-1].split("?")[0]
    track_uri = f"spotify:track:{track_id}"
    print(f"[DEBUG] Přidávám do fronty: {track_uri}")
    sp.add_to_queue(track_uri)

def handle_admin_command(msg, username):
    global BOT_ACTIVE, DEFAULT_COOLDOWN
    text = msg.text.lower()
    print(f"[DEBUG] Admin příkaz od {username}: {text}")

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

# Přihlášení do Instagramu
cl = Client()
SESSION_FILE = "session.json"

def save_session():
    with open(SESSION_FILE, "w") as f:
        json.dump(cl.get_settings(), f)
    print("[DEBUG] Session uložena.")

def load_session():
    with open(SESSION_FILE, "r") as f:
        cl.set_settings(json.load(f))
    print("[DEBUG] Session načtena ze souboru.")

# Pokus o načtení session
try:
    if os.path.exists(SESSION_FILE):
        load_session()
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    else:
        raise FileNotFoundError
except Exception as e:
    print(f"[DEBUG] Nepodařilo se načíst session ({e}), přihlašuji ručně...")
    cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    save_session()

# Přihlášení do Spotify
print("[DEBUG] Přihlašuji se k Spotify...")
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-modify-playback-state user-read-playback-state"
))

# Načtení uživatelů ve vlákně
try:
    thread = cl.direct_thread(GROUP_THREAD_ID)
    user_map = {u.pk: u.username for u in thread.users}
    print("[DEBUG] Načteno", len(user_map), "uživatelů.")
except Exception as e:
    print(f"⛔ Chyba při načítání vláka: {e}")
    user_map = {}

print("🚀 Bot je aktivní a naslouchá...")

# Hlavní smyčka
while True:
    try:
        messages = cl.direct_messages(thread_id=GROUP_THREAD_ID, amount=1)
        if not messages:
            time.sleep(5)
            continue

        msg = messages[0]

        # Kontrola duplikace
        last_msg_id = get_last_message_id()
        if msg.id == last_msg_id:
            print("[DEBUG] Žádná nová zpráva.")
            time.sleep(5)
            continue

        set_last_message_id(msg.id)  # Aktualizace ID zprávy

        user_id = msg.user_id
        username = user_map.get(user_id, str(user_id))
        text = msg.text or ""
        print(f"[DEBUG] Zpráva od {username}: {text}")

        # Admin příkazy
        if username == ADMIN_USERNAME and any(cmd in text.lower() for cmd in ["start", "stop", "set cooldown"]):
            handle_admin_command(msg, username)
            continue

        if not BOT_ACTIVE:
            print("[DEBUG] Bot je pozastaven.")
            continue

        link = extract_spotify_link(text)
        if not link:
            print("[DEBUG] Žádný Spotify odkaz.")
            continue

        if can_post(username):
            try:
                add_to_queue(link)
                update_post_time(username)
                cl.direct_like_message(msg.id)
                print("[DEBUG] ✅ Skladba přidána.")
            except Exception as e:
                print(f"❌ Chyba při přidání: {e}")
                cl.direct_send("❌ Chyba při přidání do fronty.", [user_id])
        else:
            cl.direct_send("🕒 Cooldown ještě nevypršel.", [user_id])

    except Exception as e:
        print(f"⛔ Globální chyba: {e}")

    time.sleep(5)
