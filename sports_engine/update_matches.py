import requests
import csv
import os
from datetime import datetime, timedelta


def update_matches():

    API_KEY = "326817dbace2d3e8eadc29be1d404a17"

    headers = {
        "x-apisports-key": API_KEY
    }

    url = "https://v3.football.api-sports.io/fixtures"

    allowed_leagues = {
        262: "Liga MX",
        39: "Premier League",
        140: "La Liga",
        2: "Champions League"
    }

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_PATH = os.path.join(BASE_DIR, "data", "today_matches.csv")

    matches = []

    for i in range(3):

        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")

        params = {"date": date}

        r = requests.get(url, headers=headers, params=params)

        data = r.json()

        for m in data["response"]:

            league_id = m["league"]["id"]

            if league_id in allowed_leagues:

                matches.append({
                    "home": m["teams"]["home"]["name"],
                    "away": m["teams"]["away"]["name"],
                    "league": allowed_leagues[league_id]
                })

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

    with open(DATA_PATH, "w", newline="", encoding="utf-8") as csvfile:

        fieldnames = ["home", "away", "league"]

        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        writer.writeheader()

        for m in matches:
            writer.writerow(m)

    print("✅ Partidos actualizados:", len(matches))