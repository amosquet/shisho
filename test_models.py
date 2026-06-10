import os
from dotenv import load_dotenv
load_dotenv()
from google import genai

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
for model in client.models.list():
    if 'flash' in model.name.lower():
        print(model.name)
