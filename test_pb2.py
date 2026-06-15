import os
from pocketbase import PocketBase

def test():
    from dotenv import load_dotenv
    load_dotenv()
    pb = PocketBase(os.getenv("POCKETBASE_URL"))
    pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))
    records = pb.collection("reminders").get_full_list()
    if records:
        record = records[0]
        record_dict = {}
        for k, v in record.__dict__.items():
            if not k.startswith('_'):
                record_dict[k] = v
        if not hasattr(record, "id") and getattr(record, "id", None) is not None:
            record_dict["id"] = record.id
        print(record_dict)
    else:
        print("No reminders found.")

test()
