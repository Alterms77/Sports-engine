import json
import requests

API_KEY = "326817dbace2d3e8eadc29be1d404a17"

BASE_URL = "https://v3.football.api-sports.io"

headers = {
    "x-apisports-key": API_KEY
}

leagues = {
    "liga_mx": 262,
    "premier_league": 39,
    "la_liga": 140,
    "champions": 2
}

season = 2025


def get_matches(league_id):

    url = f"{BASE_URL}/fixtures"

    params = {
        "league": league_id,
        "season": season
    }

    r = requests.get(url, headers=headers, params=params)

    data = r.json()

    return data.get("response", [])


print("Descargando partidos...")

for name, league_id in leagues.items():

    matches = get_matches(league_id)

    print(name, "partidos:", len(matches))

    with open(f"data/{name}.json", "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2)

print()
print("Datos guardados en carpeta data/")