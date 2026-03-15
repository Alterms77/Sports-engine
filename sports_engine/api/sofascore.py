"""
SofaScore unofficial REST API client.

SofaScore provides excellent real-time data (live scores, team form, H2H,
match statistics, standings) through its unofficial but publicly accessible
REST API.  No API key is required, but proper browser headers are needed.

All functions:
  - Return sensible empty values ([], {}, None) on any error
  - Cache responses in memory for CACHE_TTL seconds
  - Never raise exceptions (bot-safe)

Reference endpoints
-------------------
Live events:    GET /api/v1/sport/{sport}/events/live
Scheduled:      GET /api/v1/sport/{sport}/scheduled-events/{date}
Team search:    GET /api/v1/search/{query}
Team events:    GET /api/v1/team/{id}/events/last/0       (last 10 finished)
                GET /api/v1/team/{id}/events/next/0       (next 10)
H2H:            GET /api/v1/event/{event_id}/h2h
Match stats:    GET /api/v1/event/{event_id}/statistics
Standings:      GET /api/v1/unique-tournament/{tid}/season/{sid}/standings/total
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Endpoints ─────────────────────────────────────────────────────────────────
_BASE = "https://api.sofascore.com/api/v1"

# ── SofaScore sport slugs ─────────────────────────────────────────────────────
SPORT_SLUGS = {
    "football":          "football",
    "soccer":            "football",
    "basketball":        "basketball",
    "baseball":          "baseball",
    "american-football": "american-football",
    "tennis":            "tennis",
    "nba":               "basketball",
    "nfl":               "american-football",
    "mlb":               "baseball",
    "atp":               "tennis",
}

# ── Headers that mimic a real browser request ─────────────────────────────────
_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.sofascore.com/",
    "Origin":          "https://www.sofascore.com",
}

# ── In-memory cache ────────────────────────────────────────────────────────────
_CACHE: dict = {}
CACHE_TTL = 900   # 15 minutes (live data changes fast)
LIVE_TTL  = 60    # 1 minute for live events


def _fetch(path: str, ttl: int = CACHE_TTL) -> Optional[dict]:
    """
    GET {_BASE}/{path} with caching and graceful error handling.
    Returns parsed JSON dict or None.
    """
    url = f"{_BASE}/{path}"
    now = time.time()

    if url in _CACHE:
        data, ts = _CACHE[url]
        if now - ts < ttl:
            return data

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            _CACHE[url] = (data, now)
            logger.debug("SofaScore OK: %s", path)
            return data
        logger.debug("SofaScore %s → HTTP %d", path, resp.status_code)
    except requests.exceptions.Timeout:
        logger.debug("SofaScore timeout: %s", path)
    except Exception as exc:
        logger.debug("SofaScore error [%s]: %s", path, exc)
    return None


# ── Live events ────────────────────────────────────────────────────────────────

def get_live_events(sport: str = "football") -> list:
    """
    Return all currently live events for the given sport.

    Each item: {
        "id", "home", "away", "home_score", "away_score",
        "status", "minute", "tournament", "sport"
    }
    """
    slug = SPORT_SLUGS.get(sport.lower(), sport.lower())
    data = _fetch(f"sport/{slug}/events/live", ttl=LIVE_TTL)
    if not data:
        return []
    return [_parse_event(e, sport) for e in data.get("events", [])]


def get_scheduled_events(sport: str = "football", date: str = "") -> list:
    """
    Return all scheduled events for a sport on `date` (YYYY-MM-DD).
    If `date` is empty uses today.
    """
    if not date:
        import datetime
        date = datetime.date.today().isoformat()

    slug = SPORT_SLUGS.get(sport.lower(), sport.lower())
    data = _fetch(f"sport/{slug}/scheduled-events/{date}")
    if not data:
        return []
    return [_parse_event(e, sport) for e in data.get("events", [])]


def _parse_event(e: dict, sport: str) -> dict:
    """Normalise a raw SofaScore event dict."""
    home = e.get("homeTeam", {})
    away = e.get("awayTeam", {})
    score = e.get("homeScore", {})
    ascore = e.get("awayScore", {})
    status = e.get("status", {})
    tourn = e.get("tournament", {})

    return {
        "id":          e.get("id"),
        "sport":       sport,
        "home":        home.get("name", "?"),
        "home_id":     home.get("id"),
        "away":        away.get("name", "?"),
        "away_id":     away.get("id"),
        "home_score":  score.get("current", ""),
        "away_score":  ascore.get("current", ""),
        "status":      status.get("description", "Scheduled"),
        "status_type": status.get("type", ""),
        "minute":      e.get("time", {}).get("currentPeriodStartTimestamp"),
        "tournament":  tourn.get("name", ""),
        "country":     tourn.get("category", {}).get("name", ""),
        "start_time":  e.get("startTimestamp"),
    }


# ── Team search ────────────────────────────────────────────────────────────────

def search_team(name: str, sport: str = "football") -> Optional[dict]:
    """
    Find the first SofaScore team matching `name`.
    Returns {"id", "name", "country", "sport"} or None.
    """
    data = _fetch(f"search/{requests.utils.quote(name)}", ttl=3600)
    if not data:
        return None

    slug = SPORT_SLUGS.get(sport.lower(), sport.lower())
    for item in data.get("results", []):
        entity = item.get("entity", {})
        if entity.get("type") != "team":
            continue
        sport_slug = entity.get("sport", {}).get("slug", "")
        if sport_slug != slug:
            continue
        return {
            "id":      entity.get("id"),
            "name":    entity.get("name", ""),
            "country": entity.get("country", {}).get("name", ""),
            "sport":   sport_slug,
        }
    return None


def _get_team_id(team_name: str, sport: str = "football") -> Optional[int]:
    """Return the SofaScore team ID for a given team name, or None."""
    result = search_team(team_name, sport)
    return result["id"] if result else None


# ── Team recent form ───────────────────────────────────────────────────────────

def get_team_form(team_name: str, sport: str = "football", last_n: int = 10) -> dict:
    """
    Return recent form data for a team directly from SofaScore.

    Returned dict:
      {
        "name":     str,
        "team_id":  int | None,
        "matches":  [{"scored": int, "conceded": int, "result": "W"/"D"/"L",
                      "opponent": str, "is_home": bool, "tournament": str}, ...],
        "last5":    str,         # e.g. "WWDLW"
        "attack":   float,       # avg goals scored in last_n
        "defense":  float,       # avg goals conceded in last_n
        "source":   "sofascore",
      }
    Returns {} on any failure.
    """
    team_id = _get_team_id(team_name, sport)
    if not team_id:
        return {}

    data = _fetch(f"team/{team_id}/events/last/0")
    if not data:
        return {}

    raw_events = data.get("events", [])
    matches = []
    for e in raw_events:
        home = e.get("homeTeam", {})
        away = e.get("awayTeam", {})
        is_home = (home.get("id") == team_id)
        scored    = (e.get("homeScore", {}).get("current", 0) if is_home
                     else e.get("awayScore", {}).get("current", 0)) or 0
        conceded  = (e.get("awayScore", {}).get("current", 0) if is_home
                     else e.get("homeScore", {}).get("current", 0)) or 0
        try:
            scored   = int(scored)
            conceded = int(conceded)
        except (TypeError, ValueError):
            continue

        if scored > conceded:
            result = "W"
        elif scored == conceded:
            result = "D"
        else:
            result = "L"

        opponent = away.get("name", "?") if is_home else home.get("name", "?")
        matches.append({
            "scored":     scored,
            "conceded":   conceded,
            "result":     result,
            "opponent":   opponent,
            "is_home":    is_home,
            "tournament": e.get("tournament", {}).get("name", ""),
        })

    if not matches:
        return {}

    recent = matches[-last_n:]
    last5_str = "".join(m["result"] for m in reversed(recent[:5]))
    avg_scored   = round(sum(m["scored"]   for m in recent) / len(recent), 2)
    avg_conceded = round(sum(m["conceded"] for m in recent) / len(recent), 2)

    return {
        "name":    team_name,
        "team_id": team_id,
        "matches": recent,
        "last5":   last5_str,
        "attack":  avg_scored,
        "defense": avg_conceded,
        "source":  "sofascore",
    }


# ── Head-to-head ───────────────────────────────────────────────────────────────

def get_h2h(home_team: str, away_team: str, sport: str = "football") -> dict:
    """
    Find the most recent SofaScore event between these two teams and return
    H2H statistics.

    Returns {"total", "home_wins", "draws", "away_wins", "avg_goals",
             "home_team", "away_team", "source"} or {}
    """
    # We need an event_id to call /event/{id}/h2h
    # Strategy: search scheduled events for today/recent and find the match
    import datetime
    today = datetime.date.today()

    # Try last 3 days + today
    for delta in range(0, 10):
        d = (today - datetime.timedelta(days=delta)).isoformat()
        events = get_scheduled_events(sport, d)
        for ev in events:
            if (home_team.lower() in ev["home"].lower() or
                    ev["home"].lower() in home_team.lower()):
                if (away_team.lower() in ev["away"].lower() or
                        ev["away"].lower() in away_team.lower()):
                    return _fetch_h2h(ev["id"], home_team, away_team)

    return {}


def _fetch_h2h(event_id: int, home_team: str, away_team: str) -> dict:
    """Fetch H2H for a known SofaScore event ID."""
    data = _fetch(f"event/{event_id}/h2h", ttl=3600)
    if not data:
        return {}

    events = data.get("teamDuel", {}).get("previousEvents", [])
    if not events:
        events = data.get("previousEvents", [])

    records = []
    for e in events:
        home = e.get("homeTeam", {})
        away = e.get("awayTeam", {})
        hs = e.get("homeScore", {}).get("current", 0) or 0
        as_ = e.get("awayScore", {}).get("current", 0) or 0
        try:
            records.append((int(hs), int(as_)))
        except (TypeError, ValueError):
            pass

    if not records:
        return {}

    n = len(records)
    hw = sum(1 for hg, ag in records if hg > ag)
    d  = sum(1 for hg, ag in records if hg == ag)
    aw = n - hw - d
    avg_g = round(sum(hg + ag for hg, ag in records) / n, 1)

    return {
        "total":      n,
        "home_wins":  hw,
        "draws":      d,
        "away_wins":  aw,
        "avg_goals":  avg_g,
        "home_team":  home_team,
        "away_team":  away_team,
        "source":     "sofascore",
    }


# ── Match statistics (live/post-match) ────────────────────────────────────────

def get_match_stats(event_id: int) -> dict:
    """
    Return detailed statistics for a match (shots, possession, corners, etc.)

    Returns {"stats": [{group, stats: [{name, home, away}]}], "source"} or {}
    """
    data = _fetch(f"event/{event_id}/statistics", ttl=60)
    if not data:
        return {}

    parsed = []
    for group in data.get("statistics", []):
        stats = []
        for item in group.get("statisticsItems", []):
            stats.append({
                "name":  item.get("name", ""),
                "home":  item.get("home", ""),
                "away":  item.get("away", ""),
            })
        if stats:
            parsed.append({"group": group.get("groupName", ""), "stats": stats})

    return {"stats": parsed, "source": "sofascore"} if parsed else {}


# ── League standings ───────────────────────────────────────────────────────────

def get_standings(tournament_id: int, season_id: int) -> list:
    """
    Return the league table as a list of dicts:
      {"position", "team", "played", "wins", "draws", "losses",
       "goals_for", "goals_against", "points"}

    Common IDs (SofaScore):
      Premier League: tournament_id=17, season_id=61627
      La Liga:        tournament_id=8,  season_id=61643
      Bundesliga:     tournament_id=35, season_id=63516
      Liga MX:        tournament_id=352, season_id=63698
      Serie A:        tournament_id=23, season_id=63515
    """
    data = _fetch(
        f"unique-tournament/{tournament_id}/season/{season_id}/standings/total",
        ttl=3600,
    )
    if not data:
        return []

    rows = []
    for standing in data.get("standings", []):
        for row in standing.get("rows", []):
            team = row.get("team", {})
            rows.append({
                "position":      row.get("position"),
                "team":          team.get("name", "?"),
                "team_id":       team.get("id"),
                "played":        row.get("matches"),
                "wins":          row.get("wins"),
                "draws":         row.get("draws"),
                "losses":        row.get("losses"),
                "goals_for":     row.get("scoresFor"),
                "goals_against": row.get("scoresAgainst"),
                "points":        row.get("points"),
            })
    return rows


# ── Convenience helper ─────────────────────────────────────────────────────────

def clear_cache() -> None:
    """Invalidate the in-memory SofaScore cache."""
    _CACHE.clear()
