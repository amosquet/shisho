import os
from pocketbase import PocketBase
from dotenv import load_dotenv
import json

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

c1 = pb.collections.get_one("shisho_books")
print("shisho_books schema:", json.dumps(c1.schema, indent=2))
c2 = pb.collections.get_one("books")
print("books schema:", json.dumps(c2.schema, indent=2))
