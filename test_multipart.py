import os
import requests
from pocketbase import PocketBase
from pocketbase.client import FileUpload
from dotenv import load_dotenv

load_dotenv()
pb_url = os.getenv("POCKETBASE_URL")
url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))

b = pb.collection("books").get_list(1, 1).items[0]

class BodyDict(dict):
    def __init__(self, regular_data, file_uploads):
        super().__init__(regular_data)
        self.regular_data = regular_data
        self.file_uploads = file_uploads
    def items(self):
        for k, v in self.regular_data.items():
            yield k, v
        for k, v in self.file_uploads.items():
            yield k, v

data = {
    "title": "Multipart Test",
    "startDate": ""
}
file_uploads = {
    "cover": FileUpload(("test.txt", b"test content"))
}

payload = BodyDict(data, file_uploads)
try:
    pb.collection("books").update(b.id, payload)
    print("Success")
except Exception as e:
    print("Error:", e)
