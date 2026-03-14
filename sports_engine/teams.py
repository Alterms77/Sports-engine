import requests

API_KEY = "326817dbace2d3e8eadc29be1d404a17"

headers = {
    "x-apisports-key": API_KEY
}

url = "https://v3.football.api-sports.io/teams"

params = {
    "league": 262,
    "season": 2025
}

r = requests.get(url, headers=headers, params=params)

data = r.json()

print("Equipos encontrados:", data["results"])
print()

for team in data["response"]:
    print(team["team"]["id"], "-", team["team"]["name"])