import os
from pocketbase import PocketBase
from dotenv import load_dotenv

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

books = pb.collection("shisho_books").get_list(1, 1)
if books.items:
    book = books.items[0]
    print(f"Updating book {book.id}")
    try:
        pb.collection("shisho_books").update(book.id, {"startDate": "2024-01-01 10:00:00Z"})
        print("Updated to 2024 date.")
    except Exception as e:
        print("Failed to update to 2024:", e)
        
    try:
        pb.collection("shisho_books").update(book.id, {"startDate": ""})
        print("Updated to empty string.")
    except Exception as e:
        print("Failed to update to empty string:", e)
        
    try:
        pb.collection("shisho_books").update(book.id, {"startDate": None})
        print("Updated to None.")
    except Exception as e:
        print("Failed to update to None:", e)
