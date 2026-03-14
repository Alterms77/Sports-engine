import requests

API_KEY = "326817dbace2d3e8eadc29be1d404a17"

headers = {
    "x-apisports-key": API_KEY
}

url = "https://v3.football.api-sports.io/fixtures"

params = {
    "team": 2288,
    "season": 2025
}

r = requests.get(url, headers=headers, params=params)

data = r.json()

print("Partidos encontrados:", data["results"])
print()

for m in data["response"][:5]:

    home = m["teams"]["home"]["name"]
    away = m["teams"]["away"]["name"]

    goals_home = m["goals"]["home"]
    goals_away = m["goals"]["away"]

    print(home, goals_home, "-", goals_away, away)