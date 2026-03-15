import requests
import csv
import logging
import os
import sys

from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Allow running from repo root or from sports_engine/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL, ALLOWED_LEAGUE_IDS

# API-Sports fixture status codes that mean the game has NOT yet kicked off.
_NOT_STARTED_STATUSES = {"NS", "TBD", "PST", "SUSP", "INT"}

# API-Sports fixture status codes that mean the game is finished / irrelevant.
_FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO", "ABD", "CANC"}


def update_matches():
    """Fetch today's soccer fixtures from API-Sports and write to CSV.

    Only fixtures that have NOT yet kicked off are written so that:
    - Stale games from previous days are excluded (date filter).
    - Games that have already started or finished are excluded (status filter).

    Columns written: home, away, league, date, kickoff_utc, status
    """

    if not API_SPORTS_KEY:
        logger.warning("API_SPORTS_KEY not set – skipping live match update")
        return

    headers = {"x-apisports-key": API_SPORTS_KEY}
    url = f"{API_SPORTS_BASE_URL}/fixtures"
    allowed_leagues = ALLOWED_LEAGUE_IDS

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_PATH = os.path.join(BASE_DIR, "data", "today_matches.csv")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params = {"date": today}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("API-Sports request failed for date %s: %s", today, exc)
        return

    matches = []
    now_utc = datetime.now(timezone.utc)

    for m in data.get("response", []):
        league_id = m["league"]["id"]
        if league_id not in allowed_leagues:
            continue

        fixture = m.get("fixture", {})
        status_short = fixture.get("status", {}).get("short", "NS")

        # Skip games that have already finished
        if status_short in _FINISHED_STATUSES:
            continue

        # Also skip games that are currently live (let the next refresh pick
        # them up if they get postponed; we don't want live games in parlays)
        if status_short not in _NOT_STARTED_STATUSES:
            continue

        # Secondary guard: skip if the kickoff time is already in the past
        kickoff_str = fixture.get("date", "")  # ISO 8601, e.g. "2024-03-15T20:00:00+00:00"
        if kickoff_str:
            try:
                kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff_dt.tzinfo is None:
                    kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
                if kickoff_dt <= now_utc:
                    continue
            except (ValueError, TypeError):
                pass  # keep fixture if we can't parse the time

        matches.append({
            "home": m["teams"]["home"]["name"],
            "away": m["teams"]["away"]["name"],
            "league": allowed_leagues[league_id],
            "date": today,
            "kickoff_utc": kickoff_str,
            "status": status_short,
        })

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

    with open(DATA_PATH, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["home", "away", "league", "date", "kickoff_utc", "status"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for m in matches:
            writer.writerow(m)

    logger.info(
        "Partidos actualizados para %s: %d partido(s) sin comenzar",
        today, len(matches),
    )