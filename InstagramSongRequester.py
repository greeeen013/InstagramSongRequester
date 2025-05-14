import os
import re
import time
import json
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from instagrapi import Client
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# ========== ENVIRONMENT ==========
load_dotenv()
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
GROUP_THREAD_ID = os.getenv("GROUP_THREAD_ID")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")

DEFAULT_COOLDOWN = 60
BOT_ACTIVE = True
SESSION_FILE = "session.json"

# ========== DATABASE ==========
conn = sqlite3.connect('cooldown.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS cooldowns (user TEXT PRIMARY KEY, last_time TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)''')
conn.commit()

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
        return True
    last_time = datetime.fromisoformat(row[0])
    return datetime.now() - last_time >= timedelta(minutes=DEFAULT_COOLDOWN)

def minutes_remaining(user):
    c.execute("SELECT last_time FROM cooldowns WHERE user=?", (user,))
    row = c.fetchone()
    if not row:
        return DEFAULT_COOLDOWN
    last_time = datetime.fromisoformat(row[0])
    remaining = DEFAULT_COOLDOWN - int((datetime.now() - last_time).total_seconds() // 60)
    return max(1, remaining)

def update_post_time(user):
    c.execute("REPLACE INTO cooldowns (user, last_time) VALUES (?, ?)", (user, datetime.now().isoformat()))
    conn.commit()

# ========== INSTAGRAM LOGIN ==========
cl = Client()

def save_session():
    with open(SESSION_FILE, "w") as f:
        json.dump(cl.get_settings(), f)

def load_session():
    with open(SESSION_FILE, "r") as f:
        cl.set_settings(json.load(f))

try:
    if os.path.exists(SESSION_FILE):
        load_session()
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    else:
        raise FileNotFoundError
except Exception:
    cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    save_session()

# ========== SPOTIFY LOGIN ==========
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-modify-playback-state user-read-playback-state"
))

# ========== USER MAP ==========
try:
    thread = cl.direct_thread(GROUP_THREAD_ID)
    user_map = {u.pk: u.username for u in thread.users}
except Exception as e:
    print(f"[ERROR] Nepodařilo se načíst thread: {e}")
    user_map = {}

# ========== UTIL ==========
def extract_spotify_link(text):
    match = re.search(r'(https?://open\.spotify\.com/track/\S+)', text)
    return match.group(1) if match else None

def add_to_queue(link):
    track_id = link.split("/")[-1].split("?")[0]
    sp.add_to_queue(f"spotify:track:{track_id}")

def handle_admin_command(msg, username):
    global BOT_ACTIVE, DEFAULT_COOLDOWN
    text = msg.text.lower()

    if "stop" in text:
        BOT_ACTIVE = False
        cl.direct_send(f"@{username}: ⏸️ Bot pozastaven.", thread_ids=[GROUP_THREAD_ID])
    elif "start" in text:
        BOT_ACTIVE = True
        cl.direct_send(f"@{username}: ▶️ Bot znovu spuštěn.", thread_ids=[GROUP_THREAD_ID])
    elif "set cooldown" in text:
        try:
            value = int(re.findall(r"\d+", text)[0])
            DEFAULT_COOLDOWN = value
            cl.direct_send(f"@{username}: 🕒 Cooldown nastaven na {value} minut.", thread_ids=[GROUP_THREAD_ID])
        except:
            cl.direct_send(f"@{username}: ❌ Neplatný formát cooldownu.", thread_ids=[GROUP_THREAD_ID])

# ========== MAIN LOOP ==========
print("🚀 Bot je aktivní a naslouchá...")
while True:
    try:
        messages = cl.direct_messages(thread_id=GROUP_THREAD_ID, amount=1)
        if not messages:
            time.sleep(5)
            continue

        msg = messages[0]
        last_id = get_last_message_id()
        if msg.id == last_id:
            time.sleep(5)
            continue

        set_last_message_id(msg.id)

        user_id = msg.user_id
        username = user_map.get(user_id, str(user_id))
        text = msg.text or ""

        print(f"[DEBUG] Zpráva od @{username}: {text}")

        if username == ADMIN_USERNAME and any(cmd in text.lower() for cmd in ["start", "stop", "set cooldown"]):
            handle_admin_command(msg, username)
            continue

        if not BOT_ACTIVE:
            print("[DEBUG] Bot je pozastaven.")
            continue

        link = extract_spotify_link(text)
        if not link:
            continue

        if can_post(username):
            try:
                add_to_queue(link)
                update_post_time(username)
                cl.direct_like_message(msg.id)
                print(f"[DEBUG] ✅ Přidána skladba od @{username}")
            except Exception as e:
                cl.direct_send(f"@{username}: ❌ Chyba při přidávání do fronty.", thread_ids=[GROUP_THREAD_ID])
                print(f"[ERROR] {e}")
        else:
            mins = minutes_remaining(username)
            cl.direct_send(f"@{username}: 🕒 Zkus to znovu za {mins} minut.", thread_ids=[GROUP_THREAD_ID])

    except Exception as e:
        print(f"[ERROR] Globální chyba: {e}")

    time.sleep(5)
