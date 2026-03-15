"""
Basketball (NBA) prediction engine.

Model: Gaussian point-spread.

  expected_margin = (home_off_adj + home_def_adj) − (away_off_adj + away_def_adj) + home_adv

  home_win_prob = Φ(expected_margin / σ)     where Φ = standard-normal CDF

NBA calibration (2023-24):
  avg PPG ≈ 112.5 | home advantage ≈ 3.5 pts | σ of game margin ≈ 12.2
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── League defaults (used when live ESPN stats unavailable) ───────────────────
NBA_AVG_PPG = 112.5
NBA_AVG_OPPG = 112.5
NBA_HOME_ADV = 3.5      # points — empirical NBA average
NBA_SIGMA = 12.2        # std dev of final point margin
NBA_AVG_PACE = 100.0    # possessions per 48 min


# ── Team name alias table ─────────────────────────────────────────────────────
# Maps lowercase user input → canonical ESPN team name
_ALIASES: dict = {
    "lakers": "Los Angeles Lakers",
    "los angeles lakers": "Los Angeles Lakers",
    "la lakers": "Los Angeles Lakers",
    "celtics": "Boston Celtics",
    "boston celtics": "Boston Celtics",
    "boston": "Boston Celtics",
    "warriors": "Golden State Warriors",
    "golden state": "Golden State Warriors",
    "gsw": "Golden State Warriors",
    "heat": "Miami Heat",
    "miami heat": "Miami Heat",
    "miami": "Miami Heat",
    "bucks": "Milwaukee Bucks",
    "milwaukee": "Milwaukee Bucks",
    "nets": "Brooklyn Nets",
    "brooklyn": "Brooklyn Nets",
    "knicks": "New York Knicks",
    "new york": "New York Knicks",
    "ny knicks": "New York Knicks",
    "bulls": "Chicago Bulls",
    "chicago": "Chicago Bulls",
    "cavaliers": "Cleveland Cavaliers",
    "cavs": "Cleveland Cavaliers",
    "cleveland": "Cleveland Cavaliers",
    "76ers": "Philadelphia 76ers",
    "sixers": "Philadelphia 76ers",
    "philadelphia": "Philadelphia 76ers",
    "philly": "Philadelphia 76ers",
    "raptors": "Toronto Raptors",
    "toronto": "Toronto Raptors",
    "hawks": "Atlanta Hawks",
    "atlanta": "Atlanta Hawks",
    "hornets": "Charlotte Hornets",
    "charlotte": "Charlotte Hornets",
    "magic": "Orlando Magic",
    "orlando": "Orlando Magic",
    "wizards": "Washington Wizards",
    "washington": "Washington Wizards",
    "pacers": "Indiana Pacers",
    "indiana": "Indiana Pacers",
    "pistons": "Detroit Pistons",
    "detroit": "Detroit Pistons",
    "suns": "Phoenix Suns",
    "phoenix": "Phoenix Suns",
    "nuggets": "Denver Nuggets",
    "denver": "Denver Nuggets",
    "jazz": "Utah Jazz",
    "utah": "Utah Jazz",
    "timberwolves": "Minnesota Timberwolves",
    "wolves": "Minnesota Timberwolves",
    "minnesota": "Minnesota Timberwolves",
    "blazers": "Portland Trail Blazers",
    "trail blazers": "Portland Trail Blazers",
    "portland": "Portland Trail Blazers",
    "kings": "Sacramento Kings",
    "sacramento": "Sacramento Kings",
    "clippers": "Los Angeles Clippers",
    "la clippers": "Los Angeles Clippers",
    "thunder": "Oklahoma City Thunder",
    "okc": "Oklahoma City Thunder",
    "oklahoma city": "Oklahoma City Thunder",
    "mavericks": "Dallas Mavericks",
    "mavs": "Dallas Mavericks",
    "dallas": "Dallas Mavericks",
    "rockets": "Houston Rockets",
    "houston": "Houston Rockets",
    "grizzlies": "Memphis Grizzlies",
    "memphis": "Memphis Grizzlies",
    "pelicans": "New Orleans Pelicans",
    "new orleans": "New Orleans Pelicans",
    "spurs": "San Antonio Spurs",
    "san antonio": "San Antonio Spurs",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (no scipy needed)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def resolve_team(name: str) -> Optional[str]:
    """Return canonical NBA team name, or None if not recognised."""
    key = name.strip().lower()
    if not key:
        return None
    if key in _ALIASES:
        return _ALIASES[key]
    # Partial match
    for alias, canonical in _ALIASES.items():
        if key in alias or alias in key:
            return canonical
    return None


def suggest_teams(name: str, top_n: int = 3) -> list:
    """Return up to top_n canonical NBA team names close to the input."""
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
    """Try to fetch live season stats from ESPN; return {} on failure."""
    try:
        from api.espn_api import get_team_season_stats, get_team_record
        stats = get_team_season_stats("nba", team_name)
        record = get_team_record("nba", team_name)
        return {**stats, **record}
    except Exception as exc:
        logger.debug("ESPN stats unavailable for '%s': %s", team_name, exc)
        return {}


def _extract_ppg(stats: dict, fallback: float) -> float:
    """Extract points-per-game from ESPN stats dict, or use fallback."""
    for key in ("ppg", "pointsPerGame", "avgPoints", "points"):
        if key in stats:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                pass
    return fallback


def _extract_oppg(stats: dict, fallback: float) -> float:
    """Extract opponent points-per-game from ESPN stats dict."""
    for key in ("oppg", "opponentPointsPerGame", "avgPointsAllowed", "pointsAllowed"):
        if key in stats:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                pass
    return fallback


# ── Confidence from win probability ──────────────────────────────────────────

def _confidence(win_prob: float) -> str:
    """Map win probability (%) to ALTA/MEDIA/BAJA confidence label."""
    if win_prob >= 65:
        return "ALTA"
    elif win_prob >= 55:
        return "MEDIA"
    else:
        return "BAJA"


# ── Main prediction function ──────────────────────────────────────────────────

def predict_game(home_name: str, away_name: str) -> dict:
    """
    Predict an NBA game and return a standardised prediction dict.

    Uses ESPN live season stats when available; falls back to league averages.

    Returned keys
    -------------
    sport, home, away,
    home_win, away_win             (probabilities, %)
    expected_home, expected_away   (projected final scores)
    spread                         (expected point margin, + = home favoured)
    over_under                     (projected total points)
    confidence                     ("ALTA" / "MEDIA" / "BAJA")
    best_bet                       (human-readable recommendation)
    live_data                      (bool — True when ESPN data was used)
    home_record, away_record       (season W-L strings if available)
    """
    home_stats = _fetch_espn_stats(home_name)
    away_stats = _fetch_espn_stats(away_name)
    live = bool(home_stats or away_stats)

    home_ppg = _extract_ppg(home_stats, NBA_AVG_PPG)
    home_oppg = _extract_oppg(home_stats, NBA_AVG_OPPG)
    away_ppg = _extract_ppg(away_stats, NBA_AVG_PPG)
    away_oppg = _extract_oppg(away_stats, NBA_AVG_OPPG)

    league_avg = (home_ppg + away_ppg) / 2

    # Offensive and defensive strengths relative to league average
    home_off = home_ppg - league_avg       # positive = above-average offense
    home_def = league_avg - home_oppg      # positive = above-average defense
    away_off = away_ppg - league_avg
    away_def = league_avg - away_oppg

    expected_margin = (home_off + home_def) - (away_off + away_def) + NBA_HOME_ADV

    home_win_prob = round(_normal_cdf(expected_margin / NBA_SIGMA) * 100, 1)
    away_win_prob = round(100 - home_win_prob, 1)

    # Projected scores
    expected_home = round(league_avg + home_off - away_def + NBA_HOME_ADV / 2, 1)
    expected_away = round(league_avg + away_off - home_def - NBA_HOME_ADV / 2, 1)
    expected_home = max(85.0, expected_home)
    expected_away = max(85.0, expected_away)
    over_under = round(expected_home + expected_away, 1)

    favoured = home_name if expected_margin >= 0 else away_name
    lead_prob = home_win_prob if expected_margin >= 0 else away_win_prob
    conf = _confidence(lead_prob)

    best_bet = f"Victoria {favoured} ({lead_prob:.1f}%)"
    spread_str = (
        f"{home_name} -{abs(expected_margin):.1f}"
        if expected_margin > 0
        else f"{away_name} -{abs(expected_margin):.1f}"
    )

    # ── Extended markets and player props ─────────────────────────────────────
    from core.props import nba_quarter_projections, nba_player_props, nba_game_totals
    quarters = nba_quarter_projections(expected_home, expected_away)
    player_props = nba_player_props(home_ppg, away_ppg, home_name, away_name)
    game_totals = nba_game_totals(expected_home, expected_away)

    return {
        "sport": "NBA 🏀",
        "home": home_name,
        "away": away_name,
        "home_win": home_win_prob,
        "away_win": away_win_prob,
        "expected_home": expected_home,
        "expected_away": expected_away,
        "spread": round(expected_margin, 1),
        "spread_str": spread_str,
        "over_under": over_under,
        "confidence": conf,
        "best_bet": best_bet,
        "live_data": live,
        "home_record": home_stats.get("summary", ""),
        "away_record": away_stats.get("summary", ""),
        "home_ppg": round(home_ppg, 1),
        "home_oppg": round(home_oppg, 1),
        "away_ppg": round(away_ppg, 1),
        "away_oppg": round(away_oppg, 1),
        "quarter_projections": quarters,
        "player_props": player_props,
        "game_totals": game_totals,
    }
