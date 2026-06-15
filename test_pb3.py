import os, requests, json
pb_url = os.getenv("POCKETBASE_URL")
pb_user = os.getenv("POCKETBASE_USER")
pb_password = os.getenv("POCKETBASE_PASSWORD")
auth = requests.post(f"{pb_url}/api/admins/auth-with-password", json={"identity":pb_user, "password":pb_password}).json()
res = requests.get(f"{pb_url}/api/collections/notes/records?perPage=1", headers={"Authorization": "Bearer " + auth.get("token", "")}).json()
print(json.dumps(res, indent=2))
