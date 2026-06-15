import os, json
from pocketbase import PocketBase

# parse .env
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k] = v

pb = PocketBase(os.environ["POCKETBASE_URL"])
auth = pb.collection("users").auth_with_password(os.environ["POCKETBASE_USER"], os.environ["POCKETBASE_PASSWORD"])

body = {
    "title": "API Test Note",
    "text": "Hello API",
    "user_id": pb.auth_store.model.id
}
try:
    record = pb.collection("notes").create(body)
    print("Created successfully:", record.id)
    print("Dict:", record.__dict__)
except Exception as e:
    print("Error creating note:", e)
    if hasattr(e, "data"):
        print("Data:", e.data)
