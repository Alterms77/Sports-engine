import requests
import csv
import logging
import os
import sys

from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Allow running from repo root or from sports_engine/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL, ALLOWED_LEAGUE_IDS


def update_matches():

    if not API_SPORTS_KEY:
        logger.warning("API_SPORTS_KEY not set – skipping live match update")
        return

    headers = {
        "x-apisports-key": API_SPORTS_KEY
    }

    url = f"{API_SPORTS_BASE_URL}/fixtures"

    allowed_leagues = ALLOWED_LEAGUE_IDS

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_PATH = os.path.join(BASE_DIR, "data", "today_matches.csv")

    matches = []

    for i in range(3):

        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")

        params = {"date": date}

        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as exc:
            logger.warning("API-Sports request failed for date %s: %s", date, exc)
            continue

        for m in data.get("response", []):

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

    logger.info("Partidos actualizados: %d", len(matches))