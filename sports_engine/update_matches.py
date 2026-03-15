import requests
import csv
import logging
import os
import sys

from datetime import datetime

logger = logging.getLogger(__name__)

# Allow running from repo root or from sports_engine/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL, ALLOWED_LEAGUE_IDS


def update_matches():
    """Fetch today's soccer fixtures from API-Sports and write to CSV.

    Only fixtures scheduled for the current calendar day are written so that
    the parlay builder never picks up stale games from previous or future days.
    A ``date`` column is included in the CSV so callers can double-check the
    filter at read time.
    """

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

    # Only request today's fixtures (not a rolling window)
    today = datetime.now().strftime("%Y-%m-%d")
    params = {"date": today}

    matches = []

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("API-Sports request failed for date %s: %s", today, exc)
        return

    for m in data.get("response", []):
        league_id = m["league"]["id"]
        if league_id in allowed_leagues:
            matches.append({
                "home": m["teams"]["home"]["name"],
                "away": m["teams"]["away"]["name"],
                "league": allowed_leagues[league_id],
                "date": today,
            })

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

    with open(DATA_PATH, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["home", "away", "league", "date"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for m in matches:
            writer.writerow(m)

    logger.info("Partidos actualizados para %s: %d", today, len(matches))