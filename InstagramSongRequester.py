import os
import re
import time
import json
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import ClientError, ClientConnectionError, ClientLoginRequired
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from requests.exceptions import RequestException
import socket
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# ========== ENVIRONMENT ==========
load_dotenv()
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
GROUP_THREAD_ID = os.getenv("GROUP_THREAD_ID")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")

DEFAULT_COOLDOWN = 20
BOT_ACTIVE = True
SESSION_FILE = "session.json"

# ========== DATABASE ==========
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cooldown.sqlite3")
conn = sqlite3.connect(DB_PATH)

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


# ========== INSTAGRAM CLIENT SETUP ==========
def create_instagram_client():
    cl = Client()

    # Konfigurace pro Raspberry Pi
    cl.delay_range = [2, 5]  # Větší rozptyl mezi požadavky
    cl.request_timeout = 30  # Delší timeout

    # Nastavení user agent (důležité pro Raspberry Pi)
    cl.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    # Vypnutí některých funkcí pro stabilitu
    cl.set_locale("en_US")
    cl.set_country("US")
    cl.set_country_code(1)
    cl.set_timezone_offset(0)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            if os.path.exists(SESSION_FILE):
                with open(SESSION_FILE, "r") as f:
                    session_settings = json.load(f)
                cl.set_settings(session_settings)
                cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            else:
                cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
                save_session(cl)
            return cl
        except Exception as e:
            print(f"Pokus {attempt + 1} selhal: {str(e)}")
            if attempt == max_retries - 1:
                raise
            time.sleep(10 * (attempt + 1))  # Exponenciální backoff
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)


def save_session(client):
    with open(SESSION_FILE, "w") as f:
        json.dump(client.get_settings(), f)


try:
    cl = create_instagram_client()
    BOT_USER_ID = cl.user_id
except Exception as e:
    print(f"Failed to initialize Instagram client: {e}")
    exit(1)

# ========== SPOTIFY LOGIN ==========
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-modify-playback-state user-read-playback-state"
))

# ========== USER MAP ==========
def load_user_map():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            thread = cl.direct_thread(GROUP_THREAD_ID)
            return {u.pk: u.username for u in thread.users}
        except (ClientError, ClientConnectionError) as e:
            print(f"Attempt {attempt + 1} to load thread failed: {e}")
            if attempt == max_retries - 1:
                return {}
            time.sleep(10 * (attempt + 1))  # Exponential backoff
        except Exception as e:
            print(f"Error loading user map: {e}")
            return {}


user_map = load_user_map()
if not user_map:
    print("Warning: Could not load user map, will use user IDs instead")


# ========== UTIL FUNCTIONS ==========
def extract_spotify_link(text):
    match = re.search(r'(https?://open\.spotify\.com/track/\S+)', text)
    return match.group(1) if match else None

def reconstruct_spotify_link(text):
    pattern = r'(open\.spotify\.com/track/\S+)'
    match = re.search(pattern, text)
    if not match:
        return None
    url = match.group(1)
    if not url.startswith("https://"):
        return "https://" + url
    return url

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
last_successful_request = time.time()

while True:
    try:
        # Check if we need to wait due to rate limiting
        if time.time() - last_successful_request > 3600:  # 1 hour since last success
            print("Long delay since last success, resetting session...")
            cl = create_instagram_client()

        try:
            messages = cl.direct_messages(thread_id=GROUP_THREAD_ID, amount=1)
            last_successful_request = time.time()
        except (ClientError, ClientConnectionError, RequestException) as e:
            if "500" in str(e) or "502" in str(e) or "503" in str(e) or "504" in str(e):
                print(f"Instagram server error ({e}), waiting 60 seconds...")
                time.sleep(60)
                continue
            raise

        if not messages:
            time.sleep(2)
            continue

        msg = messages[0]

        if msg.user_id == BOT_USER_ID:
            print("[DEBUG] Zpráva od bota – ignorováno.")
            time.sleep(2)
            continue

        last_id = get_last_message_id()
        if msg.id == last_id:
            time.sleep(2)
            continue

        set_last_message_id(msg.id)

        user_id = msg.user_id
        username = user_map.get(user_id, str(user_id))
        text = msg.text or ""

        print(f"[DEBUG] Zpráva od @{username}: {text}")

        if msg.item_type == "music":
            cl.direct_send(
                f"@{username}: 🎵 Zpráva vypadá jako sdílená hudba. "
                "Pokud odkaz nejde, zkus odmazat něco před `open.spotify.com/...` a pošli znovu.",
                thread_ids=[GROUP_THREAD_ID]
            )
            print("[DEBUG] Detekován typ music – upozornění odesláno.")
            time.sleep(2)
            continue

        if username == ADMIN_USERNAME and any(cmd in text.lower() for cmd in ["start", "stop", "set cooldown"]):
            handle_admin_command(msg, username)
            continue

        if not BOT_ACTIVE:
            print("[DEBUG] Bot je pozastaven.")
            continue

        link = extract_spotify_link(text)

        if not link and "open.spotify.com" in text.lower():
            link = reconstruct_spotify_link(text)
            if link:
                print(f"[DEBUG] Rekonstruovaný odkaz: {link}")

        if not link:
            continue

        if can_post(username):
            try:
                add_to_queue(link)
                update_post_time(username)
                cl.direct_send(f"@{username}: ✅ Přidáno do fronty.", thread_ids=[GROUP_THREAD_ID])
                print(f"[DEBUG] ✅ Přidána skladba od @{username}")
            except Exception as e:
                error_text = str(e)
                if "No active device found" in error_text:
                    cl.direct_send(
                        f"@{username}: ⚠️ Nemáš zapnutý přehrávač Spotify. Spusť ho prosím a pusť si něco.",
                        thread_ids=[GROUP_THREAD_ID]
                    )
                else:
                    cl.direct_send(f"@{username}: ❌ Chyba při přidávání do fronty.", thread_ids=[GROUP_THREAD_ID])
                print(f"[ERROR] {e}")
        else:
            mins = minutes_remaining(username)
            cl.direct_send(f"@{username}: 🕒 Zkus to znovu za {mins} minut.", thread_ids=[GROUP_THREAD_ID])

    except (ClientError, ClientConnectionError, RequestException) as e:
        print(f"[ERROR] Instagram API error: {e}")
        time.sleep(60)  # Longer wait for API errors
    except Exception as e:
        print(f"[ERROR] Globální chyba: {e}")
        time.sleep(10)

    time.sleep(2)