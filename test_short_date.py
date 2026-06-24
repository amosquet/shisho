import os
from pocketbase import PocketBase
from dotenv import load_dotenv

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

try:
    r = pb.collection("books").create({
        "title": "Short Date",
        "startDate": "2024-06-18"
    })
    print("Created short date")
    pb.collection("books").delete(r.id)
except Exception as e:
    print("Error:", e)
