import os
import requests
from dotenv import load_dotenv

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
user = os.getenv("POCKETBASE_USER")
pw = os.getenv("POCKETBASE_PASSWORD")

resp = requests.post(f"{url}/api/collections/users/auth-with-password", json={"identity": user, "password": pw})
token = resp.json()["token"]

resp = requests.get(f"{url}/api/collections/shisho_books/records?perPage=1&fields=*,created,updated", headers={"Authorization": token})
print("With fields=*,created,updated:", resp.json()["items"][0].keys())

