"""
ESPN public API client — no authentication required.

Supports: NBA, NFL, MLB, NHL, Tennis (ATP/WTA), and Soccer.

All functions cache results in memory for CACHE_TTL seconds and return
gracefully (None / empty list) when the network is unavailable so the
bot never crashes due to ESPN being unreachable.
"""

import logging
import re
import time
import unicodedata
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Base URL ──────────────────────────────────────────────────────────────────
_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# ── Sport + league path segments used in all ESPN URLs ────────────────────────
SPORT_PATHS = {
    "nba": "basketball/nba",
    "wnba": "basketball/wnba",
    "nfl": "football/nfl",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
    "atp": "tennis/atp",
    "wta": "tennis/wta",
    "soccer": "soccer/all",
}

# ── In-memory cache: {cache_key: (data, timestamp)} ──────────────────────────
_CACHE: dict = {}
CACHE_TTL = 1800  # 30 minutes


def _normalize(text: str) -> str:
    """Lowercase, strip accents, and collapse whitespace for fuzzy team-name matching.

    Non-ASCII characters (e.g. accented letters) are decomposed via NFKD and
    then the accent marks are dropped, so "São Paulo" becomes "sao paulo".
    This is intentional: ESPN team names and user input are generally ASCII-safe,
    and normalising both sides prevents missed matches due to encoding differences.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_str).strip().lower()


def _fetch(url: str, params: dict = None, timeout: int = 8) -> Optional[dict]:
    """
    Fetch JSON from an ESPN URL with in-memory caching and error handling.
    Returns the parsed dict or None on any failure.
    """
    cache_key = url + str(sorted((params or {}).items()))
    now = time.time()

    if cache_key in _CACHE:
        data, ts = _CACHE[cache_key]
        if now - ts < CACHE_TTL:
            return data

    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if not resp.ok:
            logger.warning("ESPN API HTTP %s %s: %s", resp.status_code, resp.reason, url)
            return None
        data = resp.json()
        _CACHE[cache_key] = (data, now)
        logger.debug("ESPN API OK: %s", url)
        return data
    except requests.exceptions.Timeout:
        logger.warning("ESPN API timeout: %s", url)
    except requests.exceptions.RequestException as exc:
        logger.warning("ESPN API error [%s]: %s", url, exc)
    return None


# ── Scoreboard ─────────────────────────────────────────────────────────────────

def get_scoreboard(sport: str) -> list:
    """
    Return today's games for a sport as a list of dicts:
      {"sport", "home", "away", "home_score", "away_score", "status"}

    Returns [] when ESPN is unreachable or sport key is unknown.
    """
    path = SPORT_PATHS.get(sport.lower())
    if not path:
        logger.warning("Unknown sport key for ESPN: %s", sport)
        return []

    data = _fetch(f"{_BASE}/{path}/scoreboard")
    if not data:
        return []

    games = []
    for event in data.get("events", []):
        comps = event.get("competitions", [{}])
        comp = comps[0] if comps else {}
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        games.append({
            "sport": sport.upper(),
            "home": home.get("team", {}).get("displayName", "?"),
            "away": away.get("team", {}).get("displayName", "?"),
            "home_abbr": home.get("team", {}).get("abbreviation", ""),
            "away_abbr": away.get("team", {}).get("abbreviation", ""),
            "home_score": home.get("score", ""),
            "away_score": away.get("score", ""),
            "status": event.get("status", {}).get("type", {}).get("description", "Scheduled"),
            "name": event.get("name", ""),
        })
    return games


def get_all_scoreboards() -> list:
    """
    Return today's games across all main sports (NBA + NFL + MLB + Tennis).
    Useful for the /today command multi-sport listing.
    """
    all_games = []
    for sport in ("nba", "nfl", "mlb", "atp"):
        all_games.extend(get_scoreboard(sport))
    return all_games


# ── Team lookups ───────────────────────────────────────────────────────────────

def _iter_teams(data: dict):
    """Yield (id, displayName, abbreviation, location) tuples from ESPN JSON."""
    # Some endpoints wrap in sports > leagues > teams
    for sport_entry in data.get("sports", []):
        for league_entry in sport_entry.get("leagues", []):
            for team_entry in league_entry.get("teams", []):
                t = team_entry.get("team", team_entry)
                yield (
                    t.get("id", ""),
                    t.get("displayName", ""),
                    t.get("abbreviation", ""),
                    t.get("location", ""),
                    t.get("name", ""),
                )
    # Other endpoints return a flat teams array
    for team_entry in data.get("teams", []):
        t = team_entry.get("team", team_entry)
        yield (
            t.get("id", ""),
            t.get("displayName", ""),
            t.get("abbreviation", ""),
            t.get("location", ""),
            t.get("name", ""),
        )


def find_team_id(sport: str, team_name: str) -> Optional[str]:
    """
    Return ESPN team ID for the given sport and name, or None if not found.
    Performs a fuzzy match against displayName, abbreviation, location, and name.
    """
    path = SPORT_PATHS.get(sport.lower())
    if not path:
        return None

    data = _fetch(f"{_BASE}/{path}/teams")
    if not data:
        return None

    query = _normalize(team_name)
    for tid, display, abbr, location, name in _iter_teams(data):
        candidates = [
            _normalize(display),
            _normalize(abbr),
            _normalize(location),
            _normalize(name),
        ]
        if any(query == c or query in c or c in query for c in candidates if c):
            return tid
    return None


# ── Team season statistics ─────────────────────────────────────────────────────

def get_team_season_stats(sport: str, team_name: str) -> dict:
    """
    Fetch current-season statistics for a team.

    Returns a flat dict of {stat_name: value} or {} on failure.
    Common keys (sport-dependent):
      "ppg", "oppg"                      (basketball, football)
      "avgRuns", "avgRunsAllowed"        (baseball)
      "wins", "losses", "winPercent"     (all)
    """
    team_id = find_team_id(sport, team_name)
    if not team_id:
        logger.debug("ESPN: team '%s' not found for sport '%s'", team_name, sport)
        return {}

    path = SPORT_PATHS.get(sport.lower())
    stats_url = f"{_BASE}/{path}/teams/{team_id}/statistics"
    data = _fetch(stats_url)
    if not data:
        return {}

    result = {}
    # Parse nested categories > stats structure
    for category in data.get("splits", {}).get("categories", []):
        for stat in category.get("stats", []):
            key = stat.get("name") or stat.get("shortDisplayName", "")
            val = stat.get("value")
            if key and val is not None:
                result[key] = val
    # Flat statistics array (some endpoints)
    for stat in data.get("statistics", []):
        key = stat.get("name") or stat.get("shortDisplayName", "")
        val = stat.get("value")
        if key and val is not None:
            result[key] = val
    return result


# ── Team record (W-L from standings) ──────────────────────────────────────────

def get_team_record(sport: str, team_name: str) -> dict:
    """
    Return the team's season record: {"wins": int, "losses": int, "ties": int,
                                       "win_pct": float} or {} on failure.
    """
    team_id = find_team_id(sport, team_name)
    if not team_id:
        return {}

    path = SPORT_PATHS.get(sport.lower())
    data = _fetch(f"{_BASE}/{path}/teams/{team_id}")
    if not data:
        return {}

    team = data.get("team", {})
    record_items = team.get("record", {}).get("items", [])
    for item in record_items:
        summary = item.get("summary", "")  # e.g. "42-29"
        if "-" in summary:
            parts = summary.split("-")
            try:
                w, l = int(parts[0]), int(parts[1])
                t = int(parts[2]) if len(parts) > 2 else 0
                total = w + l + (t or 0)
                return {
                    "wins": w,
                    "losses": l,
                    "ties": t,
                    "win_pct": round(w / total, 3) if total else 0.5,
                    "summary": summary,
                }
            except (ValueError, IndexError):
                pass
    return {}


def clear_cache() -> None:
    """Manually invalidate the in-memory ESPN cache."""
    _CACHE.clear()
