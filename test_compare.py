import os
from pocketbase import PocketBase
from dotenv import load_dotenv

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

shisho_books = pb.collection("shisho_books").get_full_list()
books = pb.collection("books").get_full_list()

for sb in shisho_books:
    for b in books:
        if getattr(sb, "isbn", "") and getattr(sb, "isbn", "") == getattr(b, "isbn", ""):
            print("shisho:", sb.title, "start_date:", repr(getattr(sb, "start_date", "")), "updated:", repr(getattr(sb, "updated", "")))
            print("books:", b.title, "start_date:", repr(getattr(b, "start_date", "")), "updated:", repr(getattr(b, "updated", "")))
