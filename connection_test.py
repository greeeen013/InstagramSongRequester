import requests
from instagrapi import Client
import os
from dotenv import load_dotenv

load_dotenv()
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

def test_connection():
    print("Testování základního HTTP připojení...")
    try:
        response = requests.get("https://i.instagram.com", timeout=10)
        print(f"HTTP Status: {response.status_code}")
    except Exception as e:
        print(f"Chyba HTTP připojení: {e}")

    print("\nTestování Instagram API...")
    cl = Client()
    try:
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
        print("Přihlášení úspěšné!")
    except Exception as e:
        print(f"Chyba Instagram API: {e}")

test_connection()