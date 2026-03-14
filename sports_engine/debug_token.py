import requests

TOKEN = "326817dbace2d3e8eadc29be1d404a17"

headers = {
    "X-Auth-Token": TOKEN
}

url = "https://api.football-data.org/v4/competitions"

r = requests.get(url, headers=headers)

print("Status:", r.status_code)
print(r.text)