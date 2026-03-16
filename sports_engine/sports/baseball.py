"""
Baseball (MLB) prediction engine.

Model: Poisson expected-runs + Pythagorean win expectation.

  xR_home = home_team_RPG × (away_team_OPPG / league_avg) × home_advantage
  xR_away = away_team_RPG × (home_team_OPPG / league_avg)

  win_prob = xR_home^α / (xR_home^α + xR_away^α)   — Pythagorean, α = 1.83

MLB calibration (2023-24):
  avg RPG ≈ 4.5 | home advantage ≈ +0.15 runs | Pythagorean exponent = 1.83
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── League constants ──────────────────────────────────────────────────────────
MLB_AVG_RPG = 4.5       # average runs per game per team
MLB_HOME_ADV = 0.15     # home advantage in expected runs
MLB_PYTH_EXP = 1.83     # Pythagorean expectation exponent

# ── Team alias table ──────────────────────────────────────────────────────────
_ALIASES: dict = {
    "yankees": "New York Yankees",
    "new york yankees": "New York Yankees",
    "ny yankees": "New York Yankees",
    "red sox": "Boston Red Sox",
    "boston red sox": "Boston Red Sox",
    "boston": "Boston Red Sox",
    "dodgers": "Los Angeles Dodgers",
    "los angeles dodgers": "Los Angeles Dodgers",
    "la dodgers": "Los Angeles Dodgers",
    "cubs": "Chicago Cubs",
    "chicago cubs": "Chicago Cubs",
    "white sox": "Chicago White Sox",
    "chicago white sox": "Chicago White Sox",
    "cws": "Chicago White Sox",
    "chi white sox": "Chicago White Sox",
    "chicago sox": "Chicago White Sox",
    "chi sox": "Chicago White Sox",
    "giants": "San Francisco Giants",
    "san francisco": "San Francisco Giants",
    "sf giants": "San Francisco Giants",
    "braves": "Atlanta Braves",
    "atlanta braves": "Atlanta Braves",
    "atlanta": "Atlanta Braves",
    "astros": "Houston Astros",
    "houston astros": "Houston Astros",
    "houston": "Houston Astros",
    "cardinals": "St. Louis Cardinals",
    "st louis": "St. Louis Cardinals",
    "st. louis": "St. Louis Cardinals",
    "mets": "New York Mets",
    "new york mets": "New York Mets",
    "ny mets": "New York Mets",
    "phillies": "Philadelphia Phillies",
    "philadelphia": "Philadelphia Phillies",
    "nationals": "Washington Nationals",
    "washington": "Washington Nationals",
    "marlins": "Miami Marlins",
    "miami marlins": "Miami Marlins",
    "miami": "Miami Marlins",
    "padres": "San Diego Padres",
    "san diego": "San Diego Padres",
    "rockies": "Colorado Rockies",
    "colorado": "Colorado Rockies",
    "diamondbacks": "Arizona Diamondbacks",
    "dbacks": "Arizona Diamondbacks",
    "arizona": "Arizona Diamondbacks",
    "pirates": "Pittsburgh Pirates",
    "pittsburgh": "Pittsburgh Pirates",
    "reds": "Cincinnati Reds",
    "cincinnati": "Cincinnati Reds",
    "brewers": "Milwaukee Brewers",
    "milwaukee": "Milwaukee Brewers",
    "twins": "Minnesota Twins",
    "minnesota": "Minnesota Twins",
    "royals": "Kansas City Royals",
    "kansas city": "Kansas City Royals",
    "tigers": "Detroit Tigers",
    "detroit": "Detroit Tigers",
    "indians": "Cleveland Guardians",
    "guardians": "Cleveland Guardians",
    "cleveland": "Cleveland Guardians",
    "rangers": "Texas Rangers",
    "texas": "Texas Rangers",
    "angels": "Los Angeles Angels",
    "los angeles angels": "Los Angeles Angels",
    "la angels": "Los Angeles Angels",
    "anaheim": "Los Angeles Angels",
    "athletics": "Oakland Athletics",
    "oakland": "Oakland Athletics",
    "a's": "Oakland Athletics",
    "mariners": "Seattle Mariners",
    "seattle": "Seattle Mariners",
    "rays": "Tampa Bay Rays",
    "tampa bay": "Tampa Bay Rays",
    "orioles": "Baltimore Orioles",
    "baltimore": "Baltimore Orioles",
    "blue jays": "Toronto Blue Jays",
    "toronto": "Toronto Blue Jays",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def resolve_team(name: str) -> Optional[str]:
    key = name.strip().lower()
    if not key:
        return None
    if key in _ALIASES:
        return _ALIASES[key]
    for alias, canonical in _ALIASES.items():
        if key in alias or alias in key:
            return canonical
    return None


def suggest_teams(name: str, top_n: int = 3) -> list:
    key = name.strip().lower()
    seen: set = set()
    results = []
    for alias, canonical in _ALIASES.items():
        if canonical not in seen and (key in alias or alias in key):
            seen.add(canonical)
            results.append(canonical)
            if len(results) >= top_n:
                break
    return results


def _fetch_espn_stats(team_name: str) -> dict:
    try:
        from api.espn_api import get_team_season_stats, get_team_record
        stats = get_team_season_stats("mlb", team_name)
        record = get_team_record("mlb", team_name)
        return {**stats, **record}
    except Exception as exc:
        logger.debug("ESPN MLB stats unavailable for '%s': %s", team_name, exc)
        return {}


def _fetch_stats(team_name: str) -> dict:
    """
    Fetch MLB team stats, preferring Sportradar when configured.

    Falls back to ESPN if Sportradar is unavailable or returns no data.
    """
    try:
        from api.sportradar import get_mlb_team_stats, is_available
        if is_available():
            sr = get_mlb_team_stats(team_name)
            if sr:
                return sr
    except Exception as exc:
        logger.debug("Sportradar MLB unavailable for '%s': %s", team_name, exc)
    return _fetch_espn_stats(team_name)


def _extract_rpg(stats: dict, fallback: float) -> float:
    for key in ("avgRuns", "runsPerGame", "runs", "rpg"):
        if key in stats:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                pass
    return fallback


def _extract_era(stats: dict) -> Optional[float]:
    for key in ("ERA", "era", "earnedRunAvg"):
        if key in stats:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                pass
    return None


def _confidence(win_prob: float) -> str:
    if win_prob >= 65:
        return "ALTA"
    elif win_prob >= 55:
        return "MEDIA"
    else:
        return "BAJA"


# ── Main prediction ───────────────────────────────────────────────────────────

def predict_game(home_name: str, away_name: str) -> dict:
    """
    Predict an MLB game using the Poisson runs model and Pythagorean win expectation.

    Data priority:
    1. Sportradar team stats + today's starting pitcher ERA/WHIP (best quality)
    2. ESPN team season stats + team ERA (fallback)
    3. League-average defaults (worst case — data unavailable)

    Starting pitcher ERA is the single most important factor for individual
    MLB game predictions; using the scheduled starter vs team ERA alone gives
    a substantial improvement in accuracy.

    Returns a standardised prediction dict compatible with the bot's formatter.
    """
    home_stats = _fetch_stats(home_name)
    away_stats = _fetch_stats(away_name)
    live = bool(home_stats or away_stats)

    home_rpg = _extract_rpg(home_stats, MLB_AVG_RPG)
    away_rpg = _extract_rpg(away_stats, MLB_AVG_RPG)

    # Use fixed league average so offensive strength is measured against
    # the full MLB baseline, not just these two teams.
    league_avg = MLB_AVG_RPG

    # Offensive strength relative to league average
    home_off_str = home_rpg / max(league_avg, 0.1)
    away_off_str = away_rpg / max(league_avg, 0.1)

    # Expected runs: team's offensive strength × opponent's "allowed" factor + home bonus
    xr_home = home_off_str * league_avg * (1.0 + MLB_HOME_ADV)
    xr_away = away_off_str * league_avg

    # ── ERA adjustment: prefer today's starting pitcher over team ERA ─────────
    #
    # Sportradar's daily schedule includes the probable starter for each game
    # with their season-to-date ERA and WHIP.  Using the starter ERA (instead
    # of the team's aggregate ERA) gives a much more accurate picture of how
    # many runs each team is actually likely to allow today.
    #
    # Adjustment formula: pitcher_adj = league_ERA / pitcher_ERA
    #   → A pitcher with ERA 2.5 vs league 4.0 allows 60 % as many runs as
    #     average, so xR against them is multiplied by 0.625.
    #   → A pitcher with ERA 5.5 allows 127 % of average runs, so xR ×1.27.

    league_era = 4.0
    starters: dict = {}
    starter_era_used = False

    try:
        from api.sportradar import get_mlb_today_starters, is_available
        if is_available():
            starters = get_mlb_today_starters(home_name, away_name)
    except Exception as exc:
        logger.debug("Sportradar MLB starters unavailable: %s", exc)

    home_pitcher_info = starters.get("home_pitcher")
    away_pitcher_info = starters.get("away_pitcher")

    # ERA used to adjust expected runs for each team
    # Priority: today's starter ERA → team season ERA → league default
    away_era_used = (
        (away_pitcher_info or {}).get("era")
        or _extract_era(away_stats)
    )
    home_era_used = (
        (home_pitcher_info or {}).get("era")
        or _extract_era(home_stats)
    )

    # Fallback variables for return dict compatibility
    away_era = away_era_used
    home_era = home_era_used

    if away_era_used:
        pitcher_adj = league_era / max(away_era_used, 0.5)
        xr_home = round(xr_home * pitcher_adj, 2)
        if away_pitcher_info and away_pitcher_info.get("era"):
            starter_era_used = True
    if home_era_used:
        pitcher_adj = league_era / max(home_era_used, 0.5)
        xr_away = round(xr_away * pitcher_adj, 2)
        if home_pitcher_info and home_pitcher_info.get("era"):
            starter_era_used = True

    xr_home = round(max(xr_home, 0.5), 2)
    xr_away = round(max(xr_away, 0.5), 2)

    # Win-record quality adjustment: win% difference shifts expected runs.
    # Coefficient 0.3 means a 50 pp win-rate gap adjusts xR by ±0.68 runs
    # (0.50 × 4.5 × 0.3), which is about 15 % of average team RPG — a
    # modest signal that avoids over-fitting to small sample win records.
    home_win_pct = home_stats.get("win_pct", 0.5)
    away_win_pct = away_stats.get("win_pct", 0.5)
    win_quality_runs = (home_win_pct - away_win_pct) * league_avg * 0.3
    xr_home = round(max(xr_home + win_quality_runs, 0.5), 2)
    xr_away = round(max(xr_away - win_quality_runs, 0.5), 2)

    # Pythagorean win expectation
    denom = xr_home ** MLB_PYTH_EXP + xr_away ** MLB_PYTH_EXP
    if denom == 0:
        home_win_prob = 50.0
    else:
        home_win_prob = round((xr_home ** MLB_PYTH_EXP / denom) * 100, 1)
    away_win_prob = round(100 - home_win_prob, 1)

    over_under = round(xr_home + xr_away, 1)

    favoured = home_name if home_win_prob >= away_win_prob else away_name
    lead_prob = max(home_win_prob, away_win_prob)
    conf = _confidence(lead_prob)
    best_bet = f"Victoria {favoured} ({lead_prob:.1f}%)"

    # ── Extended markets and player props ─────────────────────────────────────
    from core.props import mlb_player_props, mlb_run_line
    player_props = mlb_player_props(xr_home, xr_away)
    run_line = mlb_run_line(xr_home, xr_away)

    return {
        "sport": "MLB ⚾",
        "home": home_name,
        "away": away_name,
        "home_win": home_win_prob,
        "away_win": away_win_prob,
        "expected_home": xr_home,
        "expected_away": xr_away,
        "spread": round(xr_home - xr_away, 2),
        "over_under": over_under,
        "confidence": conf,
        "best_bet": best_bet,
        "live_data": live,
        "home_record": home_stats.get("summary", ""),
        "away_record": away_stats.get("summary", ""),
        "home_win_pct": round(home_win_pct, 3),
        "away_win_pct": round(away_win_pct, 3),
        "home_era": home_era,
        "away_era": away_era,
        # pitcher_home/away: True when ERA data was available for that team,
        # False otherwise. Used by score_risk_mlb to assess data quality.
        "pitcher_home": home_era is not None,
        "pitcher_away": away_era is not None,
        "player_props": player_props,
        "run_line": run_line,
    }
