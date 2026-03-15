import requests
import os
import sys
from datetime import datetime, timedelta

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL, ALLOWED_LEAGUE_IDS

headers = {
    "x-apisports-key": API_SPORTS_KEY
}

url = f"{API_SPORTS_BASE_URL}/fixtures"

allowed_leagues = list(ALLOWED_LEAGUE_IDS.keys())


def get_daily_matches():

    matches = []

    for i in range(3):

        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")

        params = {
            "date": date
        }

        r = requests.get(url, headers=headers, params=params)

        data = r.json()

        for m in data["response"]:

            if m["league"]["id"] in allowed_leagues:

                league = m["league"]["name"]
                home = m["teams"]["home"]["name"]
                away = m["teams"]["away"]["name"]

                matches.append(f"{league}: {home} vs {away}")

    return matches