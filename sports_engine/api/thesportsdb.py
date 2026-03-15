"""
TheSportsDB free-tier API client.

TheSportsDB provides a completely free, publicly documented API (v1, API key "3")
that covers football, basketball, baseball, American football, and tennis.

Free endpoints used here:
  - Team search:            /searchteams.php?t={name}
  - Last 5 events by team:  /eventslast5.php?id={team_id}
  - Next 5 events by team:  /eventsnext5.php?id={team_id}
  - League table:           /lookuptable.php?l={league_id}&s={season}
  - Event statistics:       /lookupevent.php?id={event_id}

Reference: https://www.thesportsdb.com/api.php
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE = "https://www.thesportsdb.com/api/v1/json/3"

# ── In-memory cache ────────────────────────────────────────────────────────────
_CACHE: dict = {}
CACHE_TTL = 1800  # 30 minutes


def _fetch(endpoint: str, params: dict = None, ttl: int = CACHE_TTL) -> Optional[dict]:
    """GET {_BASE}/{endpoint} with caching and graceful error handling."""
    url = f"{_BASE}/{endpoint}"
    cache_key = url + str(sorted((params or {}).items()))
    now = time.time()

    if cache_key in _CACHE:
        data, ts = _CACHE[cache_key]
        if now - ts < ttl:
            return data

    try:
        resp = requests.get(url, params=params, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            _CACHE[cache_key] = (data, now)
            logger.debug("TheSportsDB OK: %s", endpoint)
            return data
        logger.debug("TheSportsDB %s → HTTP %d", endpoint, resp.status_code)
    except requests.exceptions.Timeout:
        logger.debug("TheSportsDB timeout: %s", endpoint)
    except Exception as exc:
        logger.debug("TheSportsDB error [%s]: %s", endpoint, exc)
    return None


# ── Team lookup ────────────────────────────────────────────────────────────────

def search_team(name: str) -> Optional[dict]:
    """
    Find the first team matching `name`.
    Returns {"id", "name", "sport", "league", "country"} or None.
    """
    data = _fetch("searchteams.php", {"t": name}, ttl=3600)
    if not data:
        return None
    teams = data.get("teams") or []
    if not teams:
        return None
    t = teams[0]
    return {
        "id":      t.get("idTeam"),
        "name":    t.get("strTeam", ""),
        "sport":   t.get("strSport", ""),
        "league":  t.get("strLeague", ""),
        "country": t.get("strCountry", ""),
        "badge":   t.get("strTeamBadge", ""),
    }


# ── Recent results ─────────────────────────────────────────────────────────────

def get_last_results(team_name: str, n: int = 5) -> list:
    """
    Return the last N results for a team.

    Each result: {"date", "home", "away", "home_score", "away_score",
                  "result" (W/D/L from home perspective), "league"}
    """
    team = search_team(team_name)
    if not team:
        return []

    data = _fetch("eventslast5.php", {"id": team["id"]})
    if not data:
        return []

    events = data.get("results") or []
    results = []
    for e in events[:n]:
        hs = e.get("intHomeScore")
        as_ = e.get("intAwayScore")
        if hs is None or as_ is None:
            continue
        try:
            hs, as_ = int(hs), int(as_)
        except (TypeError, ValueError):
            continue
        home_team = e.get("strHomeTeam", "")
        away_team = e.get("strAwayTeam", "")
        is_home = team["name"].lower() in home_team.lower()
        if is_home:
            result = "W" if hs > as_ else ("D" if hs == as_ else "L")
        else:
            result = "W" if as_ > hs else ("D" if hs == as_ else "L")
        results.append({
            "date":       e.get("dateEvent", ""),
            "home":       home_team,
            "away":       away_team,
            "home_score": hs,
            "away_score": as_,
            "result":     result,
            "league":     e.get("strLeague", ""),
        })
    return results


# ── Next fixtures ──────────────────────────────────────────────────────────────

def get_next_fixtures(team_name: str, n: int = 5) -> list:
    """
    Return the next N upcoming fixtures for a team.

    Each item: {"date", "home", "away", "league", "time"}
    """
    team = search_team(team_name)
    if not team:
        return []

    data = _fetch("eventsnext5.php", {"id": team["id"]})
    if not data:
        return []

    events = data.get("events") or []
    fixtures = []
    for e in events[:n]:
        fixtures.append({
            "date":   e.get("dateEvent", ""),
            "time":   e.get("strTime", ""),
            "home":   e.get("strHomeTeam", ""),
            "away":   e.get("strAwayTeam", ""),
            "league": e.get("strLeague", ""),
        })
    return fixtures


# ── League standings ───────────────────────────────────────────────────────────

# Known TheSportsDB league IDs (free tier)
LEAGUE_IDS = {
    "Premier League":    "4328",
    "La Liga":           "4335",
    "Bundesliga":        "4331",
    "Serie A":           "4332",
    "Ligue 1":           "4334",
    "Liga MX":           "4350",
    "MLS":               "4346",
    "Champions League":  "4480",
    "NBA":               "4387",
    "NFL":               "4391",
    "MLB":               "4424",
    "NHL":               "4380",
}


def get_league_table(league_name: str, season: str = "") -> list:
    """
    Return league standings as a list sorted by position.

    Each row: {"position", "team", "played", "wins", "draws", "losses",
               "goals_for", "goals_against", "points"}
    """
    league_id = LEAGUE_IDS.get(league_name)
    if not league_id:
        # Try case-insensitive match
        for name, lid in LEAGUE_IDS.items():
            if name.lower() == league_name.lower():
                league_id = lid
                break
    if not league_id:
        return []

    params = {"l": league_id}
    if season:
        params["s"] = season

    data = _fetch("lookuptable.php", params, ttl=3600)
    if not data:
        return []

    table = data.get("table") or []
    rows = []
    for row in table:
        rows.append({
            "position":      int(row.get("intRank", 0) or 0),
            "team":          row.get("strTeam", "?"),
            "played":        int(row.get("intPlayed", 0) or 0),
            "wins":          int(row.get("intWin", 0) or 0),
            "draws":         int(row.get("intDraw", 0) or 0),
            "losses":        int(row.get("intLoss", 0) or 0),
            "goals_for":     int(row.get("intGoalsFor", 0) or 0),
            "goals_against": int(row.get("intGoalsAgainst", 0) or 0),
            "points":        int(row.get("intPoints", 0) or 0),
        })
    rows.sort(key=lambda x: x["position"])
    return rows


# ── Convenience helpers ────────────────────────────────────────────────────────

def get_team_form_summary(team_name: str) -> dict:
    """
    Build a form summary dict compatible with the live_aggregator schema.

    Returns {"name", "last5", "matches", "attack", "defense", "source"} or {}
    """
    results = get_last_results(team_name, 5)
    if not results:
        return {}

    last5 = "".join(r["result"] for r in results)
    total = len(results)
    team_name_lower = team_name.lower()

    scored_list   = []
    conceded_list = []
    match_dicts   = []

    for r in results:
        is_home = team_name_lower in r["home"].lower()
        scored   = r["home_score"] if is_home else r["away_score"]
        conceded = r["away_score"] if is_home else r["home_score"]
        scored_list.append(scored)
        conceded_list.append(conceded)
        opponent = r["away"] if is_home else r["home"]
        match_dicts.append({
            "scored":     scored,
            "conceded":   conceded,
            "result":     r["result"],
            "opponent":   opponent,
            "is_home":    is_home,
            "tournament": r["league"],
        })

    return {
        "name":    team_name,
        "matches": match_dicts,
        "last5":   last5,
        "attack":  round(sum(scored_list) / total, 2),
        "defense": round(sum(conceded_list) / total, 2),
        "source":  "thesportsdb",
    }


def clear_cache() -> None:
    _CACHE.clear()
