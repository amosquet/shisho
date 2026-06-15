import os
from pocketbase import PocketBase

def test():
    pb_url = os.getenv("POCKETBASE_URL", "http://10.0.2.2:8080")
    # Actually pocketbase is at the external url in quick-curie/.env originally: https://pocketbase.amcloud.dev
    # the shisho .env has it
    from dotenv import load_dotenv
    load_dotenv()
    pb = PocketBase(os.getenv("POCKETBASE_URL"))
    try:
        pb.collection("users").auth_with_password(os.getenv("POCKETBASE_USER"), os.getenv("POCKETBASE_PASSWORD"))
        records = pb.collection("reminders").get_full_list()
        if records:
            print(records[0].__dict__)
        else:
            print("No reminders found.")
    except Exception as e:
        print(f"Error: {e}")

test()
