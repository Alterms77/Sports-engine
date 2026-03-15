"""
Live data aggregator — unified interface for all live-score sources.

Priority waterfall (best data quality wins):
  1. SofaScore  — richest data, unofficial REST API
  2. ESPN       — official free API (already in codebase)
  3. TheSportsDB — fully free, public API

Each public function returns a standardised dict.  All sources are tried
in order; the first successful result is returned.  If all sources fail
(e.g. sandbox / offline environment) a sensible empty response is returned
so the bot never crashes.

Exported functions
------------------
get_live_scores(sport)              → list of live events
get_today_schedule(sport, date)     → scheduled events for a date
get_team_live_form(team, sport)     → live form data for a team
get_league_table(league_name)       → league standings
get_next_fixtures(team)             → upcoming fixtures
format_live_event(event)            → Telegram-friendly single-line string
format_live_scoreboard(events)      → multi-line scoreboard string
"""

import logging
import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ── Lazy imports (prevent crashes if one source module has issues) ─────────────

def _sofascore():
    try:
        import api.sofascore as ss
        return ss
    except Exception:
        return None


def _thesportsdb():
    try:
        import api.thesportsdb as tsdb
        return tsdb
    except Exception:
        return None


def _espn():
    try:
        import api.espn_api as espn
        return espn
    except Exception:
        return None


# ── Standardised event schema ─────────────────────────────────────────────────
#
# {
#   "id":         str | None,
#   "sport":      str,           # e.g. "football", "nba"
#   "home":       str,
#   "away":       str,
#   "home_score": str | int,     # "" when not started
#   "away_score": str | int,
#   "status":     str,           # "Live", "Scheduled", "Finished", etc.
#   "minute":     int | None,    # current match minute (live only)
#   "tournament": str,
#   "country":    str,
#   "source":     str,           # which API provided this
# }


# ── Live scores ────────────────────────────────────────────────────────────────

def get_live_scores(sport: str = "football") -> list:
    """
    Return all currently live events for a sport.

    Tries SofaScore → ESPN in order.
    """
    # 1. SofaScore
    ss = _sofascore()
    if ss:
        try:
            events = ss.get_live_events(sport)
            if events:
                for ev in events:
                    ev["source"] = "sofascore"
                return events
        except Exception as exc:
            logger.debug("SofaScore live failed: %s", exc)

    # 2. ESPN
    espn = _espn()
    if espn:
        try:
            sport_key = _sport_to_espn_key(sport)
            if sport_key:
                raw = espn.get_scoreboard(sport_key)
                if raw:
                    return [_normalise_espn(g, sport) for g in raw]
        except Exception as exc:
            logger.debug("ESPN live failed: %s", exc)

    return []


# ── Scheduled events / today's fixture list ───────────────────────────────────

def get_today_schedule(sport: str = "football", date: str = "") -> list:
    """
    Return today's (or `date`'s) scheduled events for a sport.

    Tries SofaScore → ESPN in order.
    """
    if not date:
        date = datetime.date.today().isoformat()

    # 1. SofaScore
    ss = _sofascore()
    if ss:
        try:
            events = ss.get_scheduled_events(sport, date)
            if events:
                for ev in events:
                    ev["source"] = "sofascore"
                return events
        except Exception as exc:
            logger.debug("SofaScore schedule failed: %s", exc)

    # 2. ESPN
    espn = _espn()
    if espn:
        try:
            sport_key = _sport_to_espn_key(sport)
            if sport_key:
                raw = espn.get_scoreboard(sport_key)
                if raw:
                    return [_normalise_espn(g, sport) for g in raw]
        except Exception as exc:
            logger.debug("ESPN schedule failed: %s", exc)

    return []


def get_all_live_scores() -> list:
    """Return live events for football + NBA + NFL + MLB merged."""
    all_events = []
    for sport in ("football", "basketball", "american-football", "baseball"):
        try:
            all_events.extend(get_live_scores(sport))
        except Exception:
            pass
    return all_events


# ── Team live form ────────────────────────────────────────────────────────────

def get_team_live_form(team_name: str, sport: str = "football") -> dict:
    """
    Return live form data for a team from the best available source.

    Returned schema:
      {"name", "matches", "last5", "attack", "defense", "source"} or {}

    Tries SofaScore → TheSportsDB in order.
    """
    # 1. SofaScore (most detailed)
    ss = _sofascore()
    if ss:
        try:
            form = ss.get_team_form(team_name, sport)
            if form:
                return form
        except Exception as exc:
            logger.debug("SofaScore form failed for '%s': %s", team_name, exc)

    # 2. TheSportsDB
    tsdb = _thesportsdb()
    if tsdb:
        try:
            form = tsdb.get_team_form_summary(team_name)
            if form:
                return form
        except Exception as exc:
            logger.debug("TheSportsDB form failed for '%s': %s", team_name, exc)

    return {}


# ── League standings ──────────────────────────────────────────────────────────

def get_league_table(league_name: str) -> list:
    """
    Return league standings from the best available source.

    Tries TheSportsDB → SofaScore (known IDs) in order.
    """
    # 1. TheSportsDB (has all major leagues, free, no auth)
    tsdb = _thesportsdb()
    if tsdb:
        try:
            table = tsdb.get_league_table(league_name)
            if table:
                return table
        except Exception as exc:
            logger.debug("TheSportsDB table failed for '%s': %s", league_name, exc)

    # 2. SofaScore (known tournament/season IDs for top leagues)
    ss = _sofascore()
    if ss:
        tid, sid = _SOFASCORE_LEAGUE_IDS.get(league_name, (None, None))
        if tid:
            try:
                return ss.get_standings(tid, sid)
            except Exception as exc:
                logger.debug("SofaScore table failed for '%s': %s", league_name, exc)

    return []


# ── Upcoming fixtures ─────────────────────────────────────────────────────────

def get_next_fixtures(team_name: str) -> list:
    """
    Return the next 5 upcoming fixtures for a team.

    Tries SofaScore → TheSportsDB in order.
    """
    # 1. SofaScore — get team ID then next events
    ss = _sofascore()
    if ss:
        try:
            team_id = ss._get_team_id(team_name)
            if team_id:
                data = ss._fetch(f"team/{team_id}/events/next/0")
                if data:
                    return [ss._parse_event(e, "football")
                            for e in data.get("events", [])[:5]]
        except Exception as exc:
            logger.debug("SofaScore next failed for '%s': %s", team_name, exc)

    # 2. TheSportsDB
    tsdb = _thesportsdb()
    if tsdb:
        try:
            return tsdb.get_next_fixtures(team_name)
        except Exception as exc:
            logger.debug("TheSportsDB next failed for '%s': %s", team_name, exc)

    return []


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_live_event(ev: dict) -> str:
    """Return a single Telegram-formatted line for a live event."""
    home  = ev.get("home", "?")
    away  = ev.get("away", "?")
    hs    = ev.get("home_score", "")
    as_   = ev.get("away_score", "")
    status = ev.get("status", "")
    minute = ev.get("minute", "")
    tourn  = ev.get("tournament", "")

    is_live = "live" in status.lower() or "progress" in status.lower()

    if is_live and hs != "" and as_ != "":
        score_str = f"`{hs}-{as_}`"
        if minute:
            return f"🔴 {home} {score_str} {away} _{tourn}_"
        return f"🔴 {home} {score_str} {away}"
    elif hs != "" and as_ != "":
        return f"✅ {home} `{hs}-{as_}` {away} _{tourn}_"
    else:
        return f"⏰ {home} vs {away} _{tourn}_"


def format_live_scoreboard(events: list, max_items: int = 12) -> str:
    """Return a multi-line Telegram-formatted scoreboard for a list of events."""
    if not events:
        return "📭 No hay eventos en este momento."

    # Group by tournament
    by_tourn: dict = {}
    for ev in events:
        t = ev.get("tournament") or ev.get("country") or "Otros"
        by_tourn.setdefault(t, []).append(ev)

    lines = []
    total = 0
    for tourn, evs in list(by_tourn.items())[:6]:  # cap at 6 tournaments
        lines.append(f"*{tourn}*")
        for ev in evs[:4]:  # cap at 4 per tournament
            lines.append(f"  {format_live_event(ev)}")
            total += 1
            if total >= max_items:
                break
        lines.append("")
        if total >= max_items:
            break

    return "\n".join(lines).strip()


def format_fixture_list(fixtures: list) -> str:
    """Return a Telegram-formatted upcoming fixtures string."""
    if not fixtures:
        return "📭 Sin próximos partidos disponibles."
    lines = []
    for f in fixtures:
        date = f.get("date", "")
        time_ = f.get("time", "")
        home = f.get("home", "?")
        away = f.get("away", "?")
        league = f.get("tournament") or f.get("league", "")
        dt_str = f"{date} {time_}".strip() if time_ else date
        league_str = f" _{league}_" if league else ""
        lines.append(f"📅 `{dt_str}` — {home} vs {away}{league_str}")
    return "\n".join(lines)


def format_last_results(matches: list, team_name: str) -> str:
    """Return Telegram-formatted recent results for a team."""
    if not matches:
        return "Sin resultados recientes disponibles."
    lines = []
    for m in matches[:5]:
        r = m.get("result", "?")
        opponent = m.get("opponent", "?")
        scored = m.get("scored", 0)
        conceded = m.get("conceded", 0)
        is_home = m.get("is_home", True)
        venue = "🏠" if is_home else "✈️"
        icon = {"W": "✅", "D": "➡️", "L": "❌"}.get(r, "❓")
        lines.append(f"  {icon}{venue} vs {opponent}: `{scored}-{conceded}`")
    return "\n".join(lines)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sport_to_espn_key(sport: str) -> Optional[str]:
    """Map generic sport name to ESPN sport key."""
    mapping = {
        "football":          "soccer",
        "soccer":            "soccer",
        "basketball":        "nba",
        "nba":               "nba",
        "american-football": "nfl",
        "nfl":               "nfl",
        "baseball":          "mlb",
        "mlb":               "mlb",
        "hockey":            "nhl",
        "nhl":               "nhl",
        "tennis":            "atp",
        "atp":               "atp",
    }
    return mapping.get(sport.lower())


def _normalise_espn(g: dict, sport: str) -> dict:
    """Convert an ESPN scoreboard dict to our standard event schema."""
    return {
        "id":         None,
        "sport":      sport,
        "home":       g.get("home", "?"),
        "away":       g.get("away", "?"),
        "home_score": g.get("home_score", ""),
        "away_score": g.get("away_score", ""),
        "status":     g.get("status", "Scheduled"),
        "minute":     None,
        "tournament": sport.upper(),
        "country":    "",
        "source":     "espn",
    }


# SofaScore tournament/season IDs for the most common leagues
_SOFASCORE_LEAGUE_IDS = {
    "Premier League": (17,  61627),
    "La Liga":        (8,   61643),
    "Bundesliga":     (35,  63516),
    "Serie A":        (23,  63515),
    "Ligue 1":        (34,  63520),
    "Liga MX":        (352, 63698),
}
