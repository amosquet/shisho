import os
from pocketbase import PocketBase
from dotenv import load_dotenv

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

# Create in shisho_books
r1 = pb.collection("shisho_books").create({
    "title": "Field Test",
    "startDate": "2024-01-01 10:00:00Z",
    "start_date": "2024-01-02 10:00:00Z",
    "publishDate": "2024-01-03 10:00:00Z",
    "publish_date": "2024-01-04 10:00:00Z"
})
b1 = pb.collection("shisho_books").get_one(r1.id)
print("shisho_books start_date:", b1.start_date, "publish_date:", b1.publish_date)
print(b1.__dict__)
pb.collection("shisho_books").delete(r1.id)

# Create in books
r2 = pb.collection("books").create({
    "title": "Field Test",
    "startDate": "2024-01-01 10:00:00Z",
    "start_date": "2024-01-02 10:00:00Z",
    "publishDate": "2024-01-03 10:00:00Z",
    "publish_date": "2024-01-04 10:00:00Z"
})
b2 = pb.collection("books").get_one(r2.id)
print("books start_date:", b2.start_date, "publish_date:", b2.publish_date)
print(b2.__dict__)
pb.collection("books").delete(r2.id)
