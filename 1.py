
import requests

url = "https://prod-api.lzt.market/213896003/"

headers = {
    "accept": "application/json",
    "authorization": "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.eyJzdWIiOjI2MTU0MjYsImlzcyI6Imx6dCIsImlhdCI6MTc2OTE5ODQ5NSwianRpIjoiOTE4MzM5Iiwic2NvcGUiOiJiYXNpYyByZWFkIHBvc3QgY29udmVyc2F0ZSBwYXltZW50IGludm9pY2UgY2hhdGJveCBtYXJrZXQiLCJleHAiOjE5MjY4Nzg0OTV9.h-4coumbxAbNnGdRzQhPmb8UOYxxICVm_kB-vrZlXhq_uEl44dPkH9gQi1KldJq5s2u-mez7sAStCcJHzws06Onxw2iSdtieU3KSuCyixLcryt3MXwxXTsBb45lLSrzOG5kJno_25jo4yMbPzB3SzAUIK0r05IDKVN3zjKeFijc"
}

response = requests.get(url, headers=headers)

print(response.text)