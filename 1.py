
import requests

url = "https://prod-api.lzt.market/user/2615426/orders?category_id=22&order_by=pdate_to_down"

headers = {
    "accept": "application/json",
    "authorization": "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.eyJzdWIiOjI2MTU0MjYsImlzcyI6Imx6dCIsImlhdCI6MTc2OTEyMjQzNSwianRpIjoiOTE3OTUwIiwic2NvcGUiOiJiYXNpYyByZWFkIHBvc3QgY29udmVyc2F0ZSBwYXltZW50IGludm9pY2UgY2hhdGJveCBtYXJrZXQiLCJleHAiOjE5MjY4MDI0MzV9.AFeSm1asGqoNbXtKUsMauLX5NljumPAcxjayGALPH3qinUWknODFSsvY5wUVpPku5-pZnuMWtYeXypy40RASSDZ4HrstgeWDmx8DFH11cDHUzPiOgl7r1YgrLSnwGreMA540GJQlC6fU2RolXGDwRp_uNuIZibh9RRzAYQlum3k"
}

response = requests.get(url, headers=headers)

print(response.text)