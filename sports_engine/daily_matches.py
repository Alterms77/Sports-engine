import requests
from datetime import datetime, timedelta

API_KEY = "326817dbace2d3e8eadc29be1d404a17"

headers = {
    "x-apisports-key": API_KEY
}

url = "https://v3.football.api-sports.io/fixtures"

allowed_leagues = [262, 39, 140, 2]


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