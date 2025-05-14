import os
from dotenv import load_dotenv
from instagrapi import Client

# Načti údaje z .env souboru
load_dotenv()
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

# Přihlášení
cl = Client()
cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

# Získání seznamu vláken
threads = cl.direct_threads(amount=10)

print("\nPoslední vlákna:")
for i, thread in enumerate(threads, 1):
    usernames = [user.username for user in thread.users]
    print(f"{i}. Thread ID: {thread.id}")
    print(f"   Účastníci: {', '.join(usernames)}\n")
