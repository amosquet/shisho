import requests

try:
    requests.post("http://httpbin.org/post", data={"startDate": None}, files={"file": ("test.txt", b"content")})
    print("Success")
except Exception as e:
    print("Error:", e)
