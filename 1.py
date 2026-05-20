import os
import requests

url = "https://prod-api.lzt.market/213896003/"

headers = {
    "accept": "application/json",
    "authorization": f"Bearer {os.environ.get('LZT_API_TOKEN', '')}"
}

response = requests.get(url, headers=headers)

print(response.text)
