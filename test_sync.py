import os
from pocketbase import PocketBase
from dotenv import load_dotenv
import subprocess

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

# Create a book in global books collection
b = pb.collection("books").create({
    "title": "Test Sync Book",
    "author": "Tester",
    "startDate": "2024-01-01 10:00:00.000Z"
})
print("Created global book", b.id)

# Run sync_books.py
subprocess.run([".venv/bin/python", "sync_books.py"])

# Check if it was synced to shisho_books
owner_id = os.getenv("OWNER_ID", "0")
user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{owner_id}'"})
pb_user_id = user_records[0].id

shisho_books = pb.collection("shisho_books").get_full_list(query_params={"filter": f"title='Test Sync Book'"})
if shisho_books:
    sb = shisho_books[0]
    print("Synced shisho_book start_date:", sb.start_date)
else:
    print("Not synced")

pb.collection("books").delete(b.id)
if shisho_books:
    pb.collection("shisho_books").delete(sb.id)
