import os
import time
from pocketbase import PocketBase
from dotenv import load_dotenv
import subprocess

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

# Find a book that exists in both
g_books = pb.collection("books").get_list(1, 1).items
b = g_books[0]

s_books = pb.collection("shisho_books").get_full_list(query_params={"filter": f"isbn='{b.isbn}'"})
if s_books:
    sb = s_books[0]
else:
    print("No matching shisho book")
    exit(1)

print("Original shisho_books startDate:", sb.start_date)
print("Original books startDate:", b.start_date)

# Update shisho_books with a new startDate
new_date = "2024-12-25 10:00:00.000Z"
pb.collection("shisho_books").update(sb.id, {"startDate": new_date})
time.sleep(1) # Ensure updated timestamp is different

print("Running sync_books.py")
subprocess.run([".venv/bin/python", "sync_books.py"])

b_after = pb.collection("books").get_one(b.id)
print("After sync books startDate:", b_after.start_date)

# Revert
pb.collection("shisho_books").update(sb.id, {"startDate": sb.start_date})
