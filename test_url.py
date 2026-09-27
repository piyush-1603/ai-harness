import urllib.request
import urllib.error
import json
import os

api_key = os.environ.get("GEMINI_API_KEY")
endpoint = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
payload = {
    "model": "gemini-2.5-flash",
    "messages": [{"role": "user", "content": "say hi"}],
    "temperature": 0.0,
}
data = json.dumps(payload).encode("utf-8")
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
}

req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
try:
    with urllib.request.urlopen(req, timeout=60) as response:
        body = response.read().decode("utf-8")
        print(body)
except Exception as e:
    print(type(e), str(e))
