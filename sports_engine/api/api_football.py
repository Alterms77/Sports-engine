import requests

API_KEY = "326817dbace2d3e8eadc29be1d404a17"

BASE_URL = "https://v3.football.api-sports.io"

headers = {
    "x-apisports-key": API_KEY
}

def get_matches(league_id, season):

    url = f"{BASE_URL}/fixtures"

    params = {
        "league": league_id,
        "season": season
    }

    r = requests.get(url, headers=headers, params=params)

    data = r.json()

    return data.get("response", [])