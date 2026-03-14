import requests
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL

headers = {
    "x-apisports-key": API_SPORTS_KEY
}

url = f"{API_SPORTS_BASE_URL}/fixtures"

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