import os
import requests
from dotenv import load_dotenv
import json

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
user = os.getenv("POCKETBASE_USER")
pw = os.getenv("POCKETBASE_PASSWORD")

# Login manually to get token
resp = requests.post(f"{url}/api/collections/users/auth-with-password", json={"identity": user, "password": pw})
token = resp.json()["token"]

resp = requests.get(f"{url}/api/collections?perPage=100", headers={"Authorization": token})
collections = resp.json()["items"]
for c in collections:
    if c["name"] == "shisho_books":
        print(json.dumps(c["fields"], indent=2))
        break
