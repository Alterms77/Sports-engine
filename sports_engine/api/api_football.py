import os
import sys
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SPORTS_ENGINE_DIR = os.path.dirname(_THIS_DIR)
if _SPORTS_ENGINE_DIR not in sys.path:
    sys.path.insert(0, _SPORTS_ENGINE_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL

BASE_URL = API_SPORTS_BASE_URL

headers = {
    "x-apisports-key": API_SPORTS_KEY
}

# ── In-memory cache ────────────────────────────────────────────────────────────
_CACHE: dict = {}
_TTL_STATS    = 4 * 3600   # 4 h — season stats barely change day-to-day
_TTL_FIXTURE  = 3600       # 1 h — fixture stats


def _fetch(url: str, params: dict, ttl: int = _TTL_STATS) -> Optional[dict]:
    """GET with simple in-memory caching. Returns parsed JSON or None."""
    if not API_SPORTS_KEY:
        return None
    cache_key = url + str(sorted(params.items()))
    now = time.time()
    if cache_key in _CACHE:
        data, ts = _CACHE[cache_key]
        if now - ts < ttl:
            return data
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if not resp.ok:
            logger.debug("API-Football HTTP %d: %s", resp.status_code, url)
            return None
        data = resp.json()
        _CACHE[cache_key] = (data, now)
        return data
    except Exception as exc:
        logger.debug("API-Football error [%s]: %s", url, exc)
        return None


def get_matches(league_id, season):

    url = f"{BASE_URL}/fixtures"

    params = {
        "league": league_id,
        "season": season
    }

    r = requests.get(url, headers=headers, params=params)

    data = r.json()

    return data.get("response", [])


def get_fixture_shot_stats(fixture_id: int) -> dict:
    """
    Fetch shot statistics for a specific fixture from API-Football.

    Endpoint: /fixtures/statistics?fixture={fixture_id}

    Returns a dict with keys:
        home / away — each sub-dict with:
            shots_on_goal       int
            shots_off_goal      int
            total_shots         int
            shots_insidebox     int
            shots_outsidebox    int
            blocked_shots       int
            shots_on_target     int   (alias for shots_on_goal)

    Returns ``{}`` on any failure.
    """
    if not API_SPORTS_KEY:
        return {}
    data = _fetch(
        f"{BASE_URL}/fixtures/statistics",
        {"fixture": fixture_id, "type": "Shots"},
        ttl=_TTL_FIXTURE,
    )
    if not data:
        return {}

    result: dict = {}
    for team_stats in data.get("response", [])[:2]:
        team_name = (team_stats.get("team") or {}).get("name", "").lower()
        side = "home" if "home" in team_name.lower() else "away"
        # Try to figure out side from order (first entry = home)
        if not result:
            side = "home"
        elif "home" in result:
            side = "away"

        stats_list = team_stats.get("statistics", [])
        stats_map: dict = {
            s.get("type", ""): (s.get("value") or 0)
            for s in stats_list
            if isinstance(s.get("value"), (int, float))
        }

        sog      = int(stats_map.get("Shots on Goal",      0))
        soff     = int(stats_map.get("Shots off Goal",     0))
        blocked  = int(stats_map.get("Blocked Shots",      0))
        inside   = int(stats_map.get("Shots insidebox",    0))
        outside  = int(stats_map.get("Shots outsidebox",   0))
        total    = int(stats_map.get("Total Shots",        0))
        if total == 0:
            total = sog + soff + blocked

        result[side] = {
            "shots_on_goal":    sog,
            "shots_off_goal":   soff,
            "total_shots":      total,
            "shots_insidebox":  inside,
            "shots_outsidebox": outside,
            "blocked_shots":    blocked,
            "shots_on_target":  sog,   # alias
        }

    return result


def get_team_season_shot_stats(team_id: int, league_id: int, season: int) -> dict:
    """
    Fetch a team's season-average shot statistics from API-Football.

    Endpoint: /teams/statistics?team={team_id}&league={league_id}&season={season}

    Returns a flat dict with:
        avg_shots                   float
        avg_shots_on_target         float
        avg_shots_allowed           float
        avg_shots_on_target_allowed float

    Returns ``{}`` on any failure or when shot data is absent.
    """
    if not API_SPORTS_KEY:
        return {}
    data = _fetch(
        f"{BASE_URL}/teams/statistics",
        {"team": team_id, "league": league_id, "season": season},
        ttl=_TTL_STATS,
    )
    if not data:
        return {}

    stats = (data.get("response") or {})
    shots_for     = (stats.get("shots") or {}).get("for",     {})
    shots_against = (stats.get("shots") or {}).get("against", {})

    avg_shots     = float((shots_for.get("average")     or {}).get("total",      0) or 0)
    avg_sot       = float((shots_for.get("average")     or {}).get("on",         0) or 0)
    avg_allow     = float((shots_against.get("average") or {}).get("total",      0) or 0)
    avg_sot_allow = float((shots_against.get("average") or {}).get("on",         0) or 0)

    if avg_shots <= 0:
        return {}

    return {
        "avg_shots":                   round(avg_shots, 1),
        "avg_shots_on_target":         round(avg_sot, 1),
        "avg_shots_allowed":           round(avg_allow, 1),
        "avg_shots_on_target_allowed": round(avg_sot_allow, 1),
    }
