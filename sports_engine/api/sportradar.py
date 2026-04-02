"""
Sportradar Data API client.

Supports NBA, NFL, and MLB with graceful fallback when any endpoint is
unavailable (plan restrictions, rate limits, network errors).

All public functions return ``{}`` or the sentinel ``{"home_pitcher": None,
"away_pitcher": None}`` on any failure so sport predictors can fall back to
ESPN data seamlessly without crashing.

Key improvements over ESPN alone
---------------------------------
* **NBA**: Offensive Rating / Defensive Rating / Pace (per-100-possession
  efficiency metrics used by pro analysts — much richer than raw PPG/OPPG).
* **MLB**: Today's *starting* pitcher ERA/WHIP for the specific game (the
  single biggest predictor in individual MLB game predictions).
* **NFL**: Points-per-game, points-allowed, plus per-season record quality.
* **Soccer**: Schedule + team stats (via shared soccer endpoint).

Rate limits (Sportradar trial plan)
-------------------------------------
* 1 request per second
* ~1 000 requests per month

The in-memory cache (4 h for season stats, 2 h for daily schedule, 1 h for
injuries) keeps actual API calls well within those limits even when the bot
generates multiple parlays per day.

Environment variables
---------------------
SPORTRADAR_API_KEY   Your Sportradar API key  (required to enable this module)
SPORTRADAR_ACCESS    ``"trial"`` (default) or ``""`` for a production licence
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ── Configuration helpers ──────────────────────────────────────────────────────

def _api_key() -> str:
    try:
        from core.config import SPORTRADAR_API_KEY
        return SPORTRADAR_API_KEY
    except Exception:
        import os
        return os.getenv("SPORTRADAR_API_KEY", "")


def _access_level() -> str:
    try:
        from core.config import SPORTRADAR_ACCESS
        return SPORTRADAR_ACCESS
    except Exception:
        import os
        return os.getenv("SPORTRADAR_ACCESS", "trial")


def is_available() -> bool:
    """Return True if a Sportradar API key is configured."""
    return bool(_api_key())


# ── Rate limiter (trial: 1 req/s) ─────────────────────────────────────────────

_LAST_REQ_TIME: float = 0.0
# 1.1 s gap between requests gives a 100 ms safety margin over Sportradar's
# trial limit of 1 req/s.  Production plans with higher limits can set
# SPORTRADAR_ACCESS="" and the gap still protects against accidental bursts.
_MIN_INTERVAL = 1.1


def _rate_limit() -> None:
    global _LAST_REQ_TIME
    elapsed = time.time() - _LAST_REQ_TIME
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_REQ_TIME = time.time()


# ── In-memory cache ────────────────────────────────────────────────────────────

_CACHE: dict = {}
_TTL_SEASON   = 4 * 3600   # 4 h — season stats barely change day-to-day
_TTL_SCHEDULE = 2 * 3600   # 2 h — daily schedule is set the morning of game day
_TTL_INJURIES = 3600        # 1 h — injury lists updated a few times per day


def _fetch(url: str, ttl: int = _TTL_SEASON) -> Optional[dict]:
    """
    GET ``url?api_key=…`` with caching and rate limiting.
    Returns parsed JSON dict or ``None`` on any failure.
    """
    key = _api_key()
    if not key:
        return None

    now = time.time()
    if url in _CACHE:
        data, ts = _CACHE[url]
        if now - ts < ttl:
            return data

    _rate_limit()
    try:
        resp = requests.get(url, params={"api_key": key}, timeout=10)
        if resp.status_code == 403:
            logger.debug("Sportradar 403 (plan restriction): %s", url)
            return None
        if resp.status_code == 429:
            logger.warning("Sportradar rate limit hit: %s", url)
            return None
        if not resp.ok:
            logger.debug("Sportradar HTTP %d: %s", resp.status_code, url)
            return None
        data = resp.json()
        _CACHE[url] = (data, now)
        logger.debug("Sportradar OK: %s", url)
        return data
    except requests.exceptions.Timeout:
        logger.debug("Sportradar timeout: %s", url)
    except Exception as exc:
        logger.debug("Sportradar error [%s]: %s", url, exc)
    return None


# ── URL builders ───────────────────────────────────────────────────────────────

def _nba_url(path: str) -> str:
    al = _access_level()
    return f"https://api.sportradar.com/nba/{al}/v8/en/{path}.json"


def _nfl_url(path: str) -> str:
    al = _access_level()
    return f"https://api.sportradar.com/nfl/official/{al}/v7/en/{path}.json"


def _mlb_url(path: str) -> str:
    al = _access_level()
    return f"https://api.sportradar.com/mlb/{al}/v7/en/{path}.json"


def _soccer_url(path: str) -> str:
    al = _access_level()
    return f"https://api.sportradar.com/soccer/{al}/v4/en/{path}.json"


# ── Text normalisation & fuzzy matching ────────────────────────────────────────

def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_str).strip().lower()


def _team_match(query: str, team: dict) -> bool:
    """Fuzzy match a user-supplied team name against a Sportradar team dict."""
    q = _normalize(query)
    candidates = [
        _normalize(team.get("name",   "")),
        _normalize(team.get("market", "")),
        _normalize(team.get("alias",  "")),
        _normalize(f"{team.get('market', '')} {team.get('name', '')}"),
    ]
    return any(q == c or q in c or c in q for c in candidates if c)


# ── Season helpers ─────────────────────────────────────────────────────────────

def _nba_season() -> str:
    """Current NBA season start year (e.g. '2024' for the 2024-25 season)."""
    now = datetime.now(timezone.utc)
    return str(now.year if now.month >= 10 else now.year - 1)


def _nfl_season() -> str:
    """Current NFL season year. NFL season starts in September."""
    now = datetime.now(timezone.utc)
    return str(now.year if now.month >= 9 else now.year - 1)


def _mlb_season() -> str:
    """Current MLB season year."""
    return str(datetime.now(timezone.utc).year)


# ════════════════════════════════════════════════════════════════════════════════
# NBA
# ════════════════════════════════════════════════════════════════════════════════

_NBA_TEAMS: dict = {}   # {team_id: team_dict} populated on first use


def _load_nba_teams() -> dict:
    global _NBA_TEAMS
    if _NBA_TEAMS:
        return _NBA_TEAMS
    data = _fetch(_nba_url("league/teams"), ttl=_TTL_SEASON)
    if data:
        for t in data.get("teams", []):
            _NBA_TEAMS[t.get("id", "")] = t
    return _NBA_TEAMS


def _find_nba_team_id(name: str) -> Optional[str]:
    for tid, info in _load_nba_teams().items():
        if _team_match(name, info):
            return tid
    return None


def get_nba_team_stats(team_name: str) -> dict:
    """
    Return enriched NBA statistics for *team_name* from Sportradar.

    Keys returned (superset of what ESPN's ``_fetch_espn_stats`` returns):

    ==================  ========================================================
    ppg                 Points per game (season average)
    oppg                Opponent points per game
    win_pct             Season win percentage (0.0–1.0)
    wins / losses       Win-loss record integers
    summary             "wins-losses" string
    off_rtg             Offensive rating (pts per 100 possessions) ← SR extra
    def_rtg             Defensive rating (pts allowed per 100 poss) ← SR extra
    pace                Possessions per 48-minute game ← SR extra
    net_rtg             off_rtg − def_rtg ← SR extra
    ==================  ========================================================

    Returns ``{}`` on any failure so ``basketball.predict_game`` falls back to
    ESPN season stats gracefully.
    """
    if not is_available():
        return {}
    try:
        season  = _nba_season()
        team_id = _find_nba_team_id(team_name)
        if not team_id:
            return {}

        url  = _nba_url(f"seasons/{season}/REG/teams/{team_id}/statistics")
        data = _fetch(url, ttl=_TTL_SEASON)
        if not data:
            return {}

        own     = data.get("own", {})
        avg     = own.get("average", {})
        adv     = own.get("advanced", {})
        record  = data.get("record", {})

        ppg  = float(avg.get("points",     0.0) or 0.0)
        oppg = float(avg.get("opp_points", 0.0) or 0.0)
        rebounds_pg = float(avg.get("rebounds", 0.0) or 0.0)
        assists_pg  = float(avg.get("assists",  0.0) or 0.0)
        steals_pg   = float(avg.get("steals",   0.0) or 0.0)
        blocks_pg   = float(avg.get("blocks",   0.0) or 0.0)

        off_rtg = float(adv.get("offensive_rating") or 0.0)
        def_rtg = float(adv.get("defensive_rating") or 0.0)
        pace    = float(adv.get("pace")              or 0.0)
        net_rtg = round(off_rtg - def_rtg, 2) if off_rtg and def_rtg else 0.0

        wins   = int(record.get("wins",   0) or 0)
        losses = int(record.get("losses", 0) or 0)
        total  = wins + losses
        win_pct = round(wins / total, 3) if total else 0.5

        result: dict = {
            "ppg":          round(ppg,          1),
            "oppg":         round(oppg,         1),
            "rebounds_pg":  round(rebounds_pg,  1),
            "assists_pg":   round(assists_pg,   1),
            "steals_pg":    round(steals_pg,    1),
            "blocks_pg":    round(blocks_pg,    1),
            "win_pct": win_pct,
            "wins":    wins,
            "losses":  losses,
            "summary": f"{wins}-{losses}",
        }
        # Include advanced metrics only when Sportradar returned them
        if off_rtg:
            result.update({
                "off_rtg": round(off_rtg, 1),
                "def_rtg": round(def_rtg, 1),
                "pace":    round(pace,    1),
                "net_rtg": net_rtg,
            })
        return result

    except Exception as exc:
        logger.debug("Sportradar NBA stats for '%s': %s", team_name, exc)
        return {}


# ════════════════════════════════════════════════════════════════════════════════
# MLB
# ════════════════════════════════════════════════════════════════════════════════

_MLB_TEAMS: dict = {}


def _load_mlb_teams() -> dict:
    global _MLB_TEAMS
    if _MLB_TEAMS:
        return _MLB_TEAMS
    data = _fetch(_mlb_url("league/teams"), ttl=_TTL_SEASON)
    if data:
        for t in data.get("teams", []):
            _MLB_TEAMS[t.get("id", "")] = t
    return _MLB_TEAMS


def _find_mlb_team_id(name: str) -> Optional[str]:
    for tid, info in _load_mlb_teams().items():
        if _team_match(name, info):
            return tid
    return None


def get_mlb_team_stats(team_name: str) -> dict:
    """
    Return MLB season statistics for *team_name* from Sportradar.

    Keys returned: ``rpg``, ``avgRuns``, ``era``/``ERA``, ``win_pct``,
    ``wins``, ``losses``, ``summary``.

    Returns ``{}`` on failure.
    """
    if not is_available():
        return {}
    try:
        season  = _mlb_season()
        team_id = _find_mlb_team_id(team_name)
        if not team_id:
            return {}

        url  = _mlb_url(f"seasons/{season}/REG/teams/{team_id}/statistics")
        data = _fetch(url, ttl=_TTL_SEASON)
        if not data:
            return {}

        stats    = data.get("statistics", {})
        hitting  = stats.get("hitting",  {}).get("overall", {})
        pitching = stats.get("pitching", {}).get("overall", {})
        record   = data.get("record", {})

        # Runs per game: total runs ÷ games played
        gp       = int(data.get("games_played", 0) or 0)
        total_r  = float(hitting.get("runs", 0) or 0.0)
        rpg      = round(total_r / gp, 2) if gp else float(
            hitting.get("avg_runs", 0) or 0.0
        )

        era = float(pitching.get("era") or 0.0)

        wins   = int(record.get("wins",   0) or 0)
        losses = int(record.get("losses", 0) or 0)
        total  = wins + losses
        win_pct = round(wins / total, 3) if total else 0.5

        return {
            "rpg":     rpg,
            "avgRuns": rpg,
            "era":     round(era, 2) if era else None,
            "ERA":     round(era, 2) if era else None,
            "win_pct": win_pct,
            "wins":    wins,
            "losses":  losses,
            "summary": f"{wins}-{losses}",
        }

    except Exception as exc:
        logger.debug("Sportradar MLB team stats for '%s': %s", team_name, exc)
        return {}


def get_mlb_today_starters(home_team: str, away_team: str) -> dict:
    """
    Return today's probable starting pitchers for a specific MLB matchup.

    Uses Sportradar's daily schedule which includes ``home_probable_pitcher``
    and ``away_probable_pitcher`` objects with per-pitcher season statistics.
    This is the single most predictive factor for individual MLB games —
    starter ERA/WHIP vastly outperforms team ERA.

    Returns
    -------
    dict with keys ``home_pitcher`` and ``away_pitcher``.  Each value is
    either ``None`` (pitcher unknown) or::

        {"name": str, "era": float|None, "whip": float|None, "k_per_9": float|None}

    Always returns the sentinel ``{"home_pitcher": None, "away_pitcher": None}``
    on any failure so ``baseball.predict_game`` can still use team ERA.
    """
    _EMPTY = {"home_pitcher": None, "away_pitcher": None}

    if not is_available():
        return _EMPTY
    try:
        now = datetime.now(timezone.utc)
        url  = _mlb_url(f"games/{now.year}/{now.month:02d}/{now.day:02d}/schedule")
        data = _fetch(url, ttl=_TTL_SCHEDULE)
        if not data:
            return _EMPTY

        home_q = _normalize(home_team)
        away_q = _normalize(away_team)

        for game in data.get("games", []):
            # Sportradar game objects use various field names — handle both
            g_home = _normalize(
                game.get("home_team_name", "")
                or game.get("home", {}).get("name", "")
                or game.get("home", {}).get("market", "")
            )
            g_away = _normalize(
                game.get("away_team_name", "")
                or game.get("away", {}).get("name", "")
                or game.get("away", {}).get("market", "")
            )
            home_match = home_q in g_home or g_home in home_q
            away_match = away_q in g_away or g_away in away_q
            if not (home_match and away_match):
                continue

            def _parse_pitcher(raw: Optional[dict]) -> Optional[dict]:
                if not raw:
                    return None
                fn   = raw.get("first_name", "")
                ln   = raw.get("last_name",  "")
                name = (f"{fn} {ln}".strip()
                        or raw.get("full_name", "")
                        or raw.get("preferred_name", ""))
                if not name:
                    return None

                pit = (raw.get("statistics", {}).get("pitching", {})
                       or raw.get("stats", {}).get("pitching", {}))
                era  = float(pit.get("era")  or pit.get("earned_run_avg") or 0.0)
                whip = float(pit.get("whip") or 0.0)
                k9   = float(
                    pit.get("k_per_9")
                    or pit.get("strikeouts_per_9")
                    or pit.get("so9")
                    or 0.0
                )
                return {
                    "name":    name,
                    "era":     round(era,  2) if era  else None,
                    "whip":    round(whip, 2) if whip else None,
                    "k_per_9": round(k9,   1) if k9   else None,
                }

            return {
                "home_pitcher": _parse_pitcher(
                    game.get("home_probable_pitcher")
                    or game.get("home_team_probable_pitcher")
                ),
                "away_pitcher": _parse_pitcher(
                    game.get("away_probable_pitcher")
                    or game.get("away_team_probable_pitcher")
                ),
            }

        return _EMPTY   # game not found in today's schedule

    except Exception as exc:
        logger.debug("Sportradar MLB starters: %s", exc)
        return _EMPTY


# ════════════════════════════════════════════════════════════════════════════════
# NFL
# ════════════════════════════════════════════════════════════════════════════════

_NFL_TEAMS: dict = {}


def _load_nfl_teams() -> dict:
    global _NFL_TEAMS
    if _NFL_TEAMS:
        return _NFL_TEAMS
    data = _fetch(_nfl_url("league/teams"), ttl=_TTL_SEASON)
    if data:
        for t in data.get("teams", []):
            _NFL_TEAMS[t.get("id", "")] = t
    return _NFL_TEAMS


def _find_nfl_team_id(name: str) -> Optional[str]:
    for tid, info in _load_nfl_teams().items():
        if _team_match(name, info):
            return tid
    return None


def get_nfl_team_stats(team_name: str) -> dict:
    """
    Return NFL season statistics for *team_name* from Sportradar.

    Keys returned: ``ppg``, ``oppg``, ``win_pct``, ``wins``, ``losses``,
    ``summary``.

    Returns ``{}`` on failure.
    """
    if not is_available():
        return {}
    try:
        season  = _nfl_season()
        team_id = _find_nfl_team_id(team_name)
        if not team_id:
            return {}

        url  = _nfl_url(f"seasons/{season}/REG/teams/{team_id}/statistics")
        data = _fetch(url, ttl=_TTL_SEASON)
        if not data:
            return {}

        scoring = data.get("statistics", {}).get("scoring", {})
        record  = data.get("record", {})
        gp      = int(data.get("games_played", 0) or 0)

        pts     = float(scoring.get("points",     0.0) or 0.0)
        opp_pts = float(scoring.get("opp_points", 0.0) or 0.0)
        ppg     = round(pts     / gp, 1) if gp else 0.0
        oppg    = round(opp_pts / gp, 1) if gp else 0.0

        wins   = int(record.get("wins",   0) or 0)
        losses = int(record.get("losses", 0) or 0)
        total  = wins + losses
        win_pct = round(wins / total, 3) if total else 0.5

        return {
            "ppg":     ppg,
            "oppg":    oppg,
            "win_pct": win_pct,
            "wins":    wins,
            "losses":  losses,
            "summary": f"{wins}-{losses}",
        }

    except Exception as exc:
        logger.debug("Sportradar NFL stats for '%s': %s", team_name, exc)
        return {}


# ════════════════════════════════════════════════════════════════════════════════
# Soccer — shot statistics
# ════════════════════════════════════════════════════════════════════════════════

def get_soccer_team_shot_stats(team_name: str, competition_id: str = "") -> dict:
    """
    Return season shot-volume statistics for a soccer team via Sportradar.

    Keys returned (when data is available):
        avg_shots               float  — total shots per game (season avg)
        avg_shots_on_target     float  — SoT per game
        avg_shots_allowed       float  — total shots conceded per game
        avg_shots_on_target_allowed float

    Returns ``{}`` on any failure (plan restriction, network error, missing data).

    Note: Sportradar soccer statistics depth depends on the API subscription
    level.  Trial plans may return only basic statistics.
    """
    if not is_available():
        return {}
    try:
        # Sportradar soccer competitor statistics endpoint
        # The competition_id can be the SR tournament URN, e.g. "sr:competition:17"
        # for Premier League.  When absent we try the global summary endpoint.
        norm_name = _normalize(team_name)
        comp_path = f"competitions/{competition_id}/seasons" if competition_id else ""
        # Look for competitor season statistics
        if not competition_id:
            return {}   # Cannot look up season stats without a competition context
        url = _soccer_url(f"{comp_path}/competitors/{norm_name}/statistics")
        data = _fetch(url, ttl=_TTL_SEASON)
        if not data:
            return {}

        stats = data.get("statistics", {})
        shots = stats.get("shots", {})
        if not shots:
            return {}

        gp = int(data.get("games_played", 0) or 0)
        if gp <= 0:
            return {}

        avg_shots         = round(float(shots.get("total",     0) or 0) / gp, 1)
        avg_sot           = round(float(shots.get("on_target", 0) or 0) / gp, 1)
        avg_allowed       = round(float(shots.get("total_against",     0) or 0) / gp, 1)
        avg_sot_allowed   = round(float(shots.get("on_target_against", 0) or 0) / gp, 1)

        return {
            "avg_shots":                   avg_shots,
            "avg_shots_on_target":         avg_sot,
            "avg_shots_allowed":           avg_allowed,
            "avg_shots_on_target_allowed": avg_sot_allowed,
        }
    except Exception as exc:
        logger.debug("Sportradar soccer shot stats for '%s': %s", team_name, exc)
        return {}


# ════════════════════════════════════════════════════════════════════════════════
# Utility
# ════════════════════════════════════════════════════════════════════════════════

def clear_cache() -> None:
    """Flush all in-memory caches (useful for testing)."""
    global _NBA_TEAMS, _MLB_TEAMS, _NFL_TEAMS
    _CACHE.clear()
    _NBA_TEAMS.clear()
    _MLB_TEAMS.clear()
    _NFL_TEAMS.clear()
