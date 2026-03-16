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
from core.db import is_available as _db_available
from core.db import ensure_table as _db_ensure_table
from core.db import upsert_fixtures as _db_upsert
from core.db import build_fixture_row as _build_row

# API-Sports fixture status codes that mean the game has NOT yet kicked off.
_NOT_STARTED_STATUSES = {"NS", "TBD", "PST", "SUSP", "INT"}

# API-Sports fixture status codes that mean the game is finished / irrelevant.
_FINISHED_STATUSES = {"FT", "AET", "PEN", "AWD", "WO", "ABD", "CANC"}


def update_matches(date: str = None, data_path: str = None):
    """Fetch soccer fixtures from API-Sports for *date* and write to *data_path*.

    Parameters
    ----------
    date : str, optional
        ISO date string ``"YYYY-MM-DD"`` to query.  Defaults to today (UTC).
    data_path : str, optional
        Absolute path of the CSV file to write.  Defaults to
        ``<this_dir>/data/today_matches.csv``.

    Only fixtures that have NOT yet kicked off are written so that:
    - Stale games from previous days are excluded (date filter).
    - Games that have already started or finished are excluded (status filter).

    Columns written: home, away, league, date, kickoff_utc, status

    The CSV is only overwritten when at least one valid match is found, so
    a failed or empty API response never destroys last-known-good data.
    """

    if not API_SPORTS_KEY:
        logger.warning("API_SPORTS_KEY not set – skipping live match update")
        return

    headers = {"x-apisports-key": API_SPORTS_KEY}
    url = f"{API_SPORTS_BASE_URL}/fixtures"
    allowed_leagues = ALLOWED_LEAGUE_IDS

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    if data_path is None:
        data_path = os.path.join(BASE_DIR, "data", "today_matches.csv")
    DATA_PATH = data_path

    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today = date  # alias for readability in the rest of the function
    params = {"date": today}

    logger.info("update_matches: querying fixtures for date %s", today)

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("API-Sports request failed for date %s: %s", today, exc)
        return

    # Check for API-level errors reported in the response body
    api_errors = data.get("errors", {})
    if api_errors:
        logger.warning("API-Sports returned errors for date %s: %s", today, api_errors)
        return

    raw_fixtures = data.get("response", [])
    total_from_api = len(raw_fixtures)
    logger.info("update_matches: API returned %d fixture(s) in total for %s", total_from_api, today)

    matches = []
    db_rows = []          # all allowed-league fixtures (including finished) for DB
    now_utc = datetime.now(timezone.utc)
    skipped_league = 0
    skipped_status = 0
    skipped_kickoff = 0

    for m in raw_fixtures:
        league_id = m["league"]["id"]
        if league_id not in allowed_leagues:
            skipped_league += 1
            continue

        fixture = m.get("fixture", {})
        status_short = fixture.get("status", {}).get("short", "NS")
        kickoff_str = fixture.get("date", "")  # ISO 8601, e.g. "2024-03-15T20:00:00+00:00"

        # Parse kickoff into a timezone-aware datetime for DB storage
        kickoff_dt = None
        if kickoff_str:
            try:
                kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff_dt.tzinfo is None:
                    kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                kickoff_dt = None

        # Always record fixture in DB (including finished ones) so that past
        # matches are never re-introduced into parlay generation.
        db_rows.append(
            _build_row(
                fixture_id=fixture.get("id", ""),
                home=m["teams"]["home"]["name"],
                away=m["teams"]["away"]["name"],
                league_id=league_id,
                league_name=m["league"].get("name", ""),
                kickoff_utc=kickoff_dt,
                status_short=status_short,
            )
        )

        # Skip games that have already finished (CSV only contains upcoming)
        if status_short in _FINISHED_STATUSES:
            skipped_status += 1
            continue

        # Also skip games that are currently live (let the next refresh pick
        # them up if they get postponed; we don't want live games in parlays)
        if status_short not in _NOT_STARTED_STATUSES:
            skipped_status += 1
            continue

        # Secondary guard: skip if the kickoff time is already in the past
        if kickoff_dt is not None and kickoff_dt <= now_utc:
            skipped_kickoff += 1
            continue

        matches.append({
            "home":       m["teams"]["home"]["name"],
            "away":       m["teams"]["away"]["name"],
            "league":     allowed_leagues[league_id],
            "date":       today,
            "kickoff_utc": kickoff_str,
            "status":     status_short,
            "round":      m["league"].get("round", ""),
            "tournament": m["league"].get("name", ""),
        })

    logger.info(
        "update_matches: of %d fixture(s) — %d filtered by league, %d by status, "
        "%d by past kickoff, %d ready to write",
        total_from_api, skipped_league, skipped_status, skipped_kickoff, len(matches),
    )

    # Upsert ALL allowed-league fixtures (including finished) into Postgres so
    # that parlay generation can always exclude previously-seen finished matches.
    if db_rows and _db_available():
        _db_ensure_table()
        n = _db_upsert(db_rows)
        logger.info(
            "update_matches: upserted %d fixture(s) to Postgres (total for date: %d)",
            n, len(db_rows),
        )

    if not matches:
        if total_from_api == 0:
            logger.warning(
                "update_matches: API returned 0 fixtures for %s "
                "(no games scheduled or API key exhausted)",
                today,
            )
        else:
            logger.warning(
                "update_matches: 0 valid matches for %s after filtering — "
                "existing CSV NOT overwritten "
                "(%d fixture(s) returned but %d filtered by league, "
                "%d by status, %d by past kickoff)",
                today, total_from_api, skipped_league, skipped_status, skipped_kickoff,
            )
        return

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

    with open(DATA_PATH, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["home", "away", "league", "date", "kickoff_utc", "status",
                      "round", "tournament"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for m in matches:
            writer.writerow(m)

    logger.info(
        "Partidos actualizados para %s: %d partido(s) sin comenzar",
        today, len(matches),
    )