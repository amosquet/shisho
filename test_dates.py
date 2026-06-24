import os
from pocketbase import PocketBase
from dotenv import load_dotenv

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

# Create a book with dates
try:
    record = pb.collection("shisho_books").create({
        "title": "Test Date Book",
        "author": "Tester",
        "startDate": "2024-01-01 10:00:00.000Z",
        "endDate": "2024-02-01 10:00:00.000Z",
    })
    print("Created book:", record.id)
    
    # Read the book
    b = pb.collection("shisho_books").get_one(record.id)
    print("start_date:", b.start_date)
    print("end_date:", b.end_date)
    
    # Try updating
    pb.collection("shisho_books").update(record.id, {
        "startDate": "2024-03-01 10:00:00.000Z"
    })
    b = pb.collection("shisho_books").get_one(record.id)
    print("Updated start_date:", b.start_date)
    
    # Clean up
    pb.collection("shisho_books").delete(record.id)
except Exception as e:
    print("Error:", e)
