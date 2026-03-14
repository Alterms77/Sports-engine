import requests

API_KEY = "326817dbace2d3e8eadc29be1d404a17"

headers = {
    "x-apisports-key": API_KEY
}

url = "https://v3.football.api-sports.io/fixtures"

params = {
    "date": "2026-03-13"
}

r = requests.get(url, headers=headers, params=params)

data = r.json()

print("Partidos encontrados:", data["results"])
print()

for m in data["response"][:10]:

    league = m["league"]["name"]
    home = m["teams"]["home"]["name"]
    away = m["teams"]["away"]["name"]

    print(league, ":", home, "vs", away)