"""
American Football (NFL) prediction engine.

Model: Gaussian point-spread.

  expected_margin = (home_off_adj + home_def_adj) − (away_off_adj + away_def_adj) + home_adv

  home_win_prob = Φ(expected_margin / σ)     where Φ = standard-normal CDF

NFL calibration (2023-24):
  avg PPG ≈ 22.0 | home advantage ≈ 2.5 pts | σ of game margin ≈ 14.1
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── League constants ──────────────────────────────────────────────────────────
NFL_AVG_PPG = 22.0      # avg points per game per team (2023-24 ≈ 22)
NFL_HOME_ADV = 2.5      # home field advantage in points
NFL_SIGMA = 14.1        # std dev of game margin

# ── Team alias table ──────────────────────────────────────────────────────────
_ALIASES: dict = {
    # AFC East
    "patriots": "New England Patriots",
    "new england": "New England Patriots",
    "bills": "Buffalo Bills",
    "buffalo": "Buffalo Bills",
    "dolphins": "Miami Dolphins",
    "miami dolphins": "Miami Dolphins",
    "jets": "New York Jets",
    "ny jets": "New York Jets",
    # AFC North
    "ravens": "Baltimore Ravens",
    "baltimore": "Baltimore Ravens",
    "steelers": "Pittsburgh Steelers",
    "pittsburgh": "Pittsburgh Steelers",
    "browns": "Cleveland Browns",
    "cleveland": "Cleveland Browns",
    "bengals": "Cincinnati Bengals",
    "cincinnati": "Cincinnati Bengals",
    # AFC South
    "texans": "Houston Texans",
    "houston texans": "Houston Texans",
    "houston": "Houston Texans",
    "colts": "Indianapolis Colts",
    "indianapolis": "Indianapolis Colts",
    "jaguars": "Jacksonville Jaguars",
    "jacksonville": "Jacksonville Jaguars",
    "titans": "Tennessee Titans",
    "tennessee": "Tennessee Titans",
    # AFC West
    "chiefs": "Kansas City Chiefs",
    "kansas city": "Kansas City Chiefs",
    "kc chiefs": "Kansas City Chiefs",
    "raiders": "Las Vegas Raiders",
    "las vegas": "Las Vegas Raiders",
    "oakland": "Las Vegas Raiders",
    "chargers": "Los Angeles Chargers",
    "la chargers": "Los Angeles Chargers",
    "broncos": "Denver Broncos",
    "denver": "Denver Broncos",
    # NFC East
    "cowboys": "Dallas Cowboys",
    "dallas": "Dallas Cowboys",
    "giants": "New York Giants",
    "ny giants": "New York Giants",
    "eagles": "Philadelphia Eagles",
    "philadelphia": "Philadelphia Eagles",
    "commanders": "Washington Commanders",
    "washington": "Washington Commanders",
    # NFC North
    "bears": "Chicago Bears",
    "chicago": "Chicago Bears",
    "lions": "Detroit Lions",
    "detroit": "Detroit Lions",
    "packers": "Green Bay Packers",
    "green bay": "Green Bay Packers",
    "vikings": "Minnesota Vikings",
    "minnesota": "Minnesota Vikings",
    # NFC South
    "falcons": "Atlanta Falcons",
    "atlanta": "Atlanta Falcons",
    "panthers": "Carolina Panthers",
    "carolina": "Carolina Panthers",
    "saints": "New Orleans Saints",
    "new orleans": "New Orleans Saints",
    "buccaneers": "Tampa Bay Buccaneers",
    "bucs": "Tampa Bay Buccaneers",
    "tampa bay": "Tampa Bay Buccaneers",
    # NFC West
    "cardinals": "Arizona Cardinals",
    "arizona": "Arizona Cardinals",
    "rams": "Los Angeles Rams",
    "la rams": "Los Angeles Rams",
    "49ers": "San Francisco 49ers",
    "niners": "San Francisco 49ers",
    "san francisco": "San Francisco 49ers",
    "sf 49ers": "San Francisco 49ers",
    "seahawks": "Seattle Seahawks",
    "seattle": "Seattle Seahawks",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


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
        stats = get_team_season_stats("nfl", team_name)
        record = get_team_record("nfl", team_name)
        return {**stats, **record}
    except Exception as exc:
        logger.debug("ESPN NFL stats unavailable for '%s': %s", team_name, exc)
        return {}


def _fetch_stats(team_name: str) -> dict:
    """
    Fetch NFL team stats, preferring Sportradar when configured.

    Falls back to ESPN if Sportradar is unavailable or returns no data.
    """
    try:
        from api.sportradar import get_nfl_team_stats, is_available
        if is_available():
            sr = get_nfl_team_stats(team_name)
            if sr:
                return sr
    except Exception as exc:
        logger.debug("Sportradar NFL unavailable for '%s': %s", team_name, exc)
    return _fetch_espn_stats(team_name)


def _extract_ppg(stats: dict, fallback: float) -> float:
    for key in ("ppg", "pointsPerGame", "avgPoints", "points", "totalPointsPerGame"):
        if key in stats:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                pass
    return fallback


def _extract_oppg(stats: dict, fallback: float) -> float:
    for key in ("oppg", "opponentPointsPerGame", "avgPointsAllowed", "pointsAllowed"):
        if key in stats:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                pass
    return fallback


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
    Predict an NFL game using a Gaussian point-spread model.

    Fetches live season stats from ESPN when available; falls back to
    league averages (neutral game prediction) otherwise.
    """
    home_stats = _fetch_stats(home_name)
    away_stats = _fetch_stats(away_name)
    live = bool(home_stats or away_stats)

    home_ppg = _extract_ppg(home_stats, NFL_AVG_PPG)
    home_oppg = _extract_oppg(home_stats, NFL_AVG_PPG)
    away_ppg = _extract_ppg(away_stats, NFL_AVG_PPG)
    away_oppg = _extract_oppg(away_stats, NFL_AVG_PPG)

    # Use the fixed league average so strengths are measured against the
    # full NFL, not just these two teams.
    league_avg = NFL_AVG_PPG

    home_off = home_ppg - league_avg
    home_def = league_avg - home_oppg
    away_off = away_ppg - league_avg
    away_def = league_avg - away_oppg

    # Win-record quality adjustment: win% difference shifts expected margin.
    # Coefficient 0.35 < NBA's 0.40 because NFL outcomes carry more variance
    # (σ = 14.1 vs 12.2), so win% is a slightly noisier quality signal.
    # A 50 pp win-rate gap shifts margin by ~2.5 pts (0.50 × 14.1 × 0.35).
    home_win_pct = home_stats.get("win_pct", 0.5)
    away_win_pct = away_stats.get("win_pct", 0.5)
    win_quality_adj = (home_win_pct - away_win_pct) * NFL_SIGMA * 0.35

    expected_margin = (
        (home_off + home_def) - (away_off + away_def)
        + NFL_HOME_ADV
        + win_quality_adj
    )

    home_win_prob = round(_normal_cdf(expected_margin / NFL_SIGMA) * 100, 1)
    away_win_prob = round(100 - home_win_prob, 1)

    expected_home = round(league_avg + home_off - away_def + NFL_HOME_ADV / 2, 1)
    expected_away = round(league_avg + away_off - home_def - NFL_HOME_ADV / 2, 1)
    expected_home = max(10.0, expected_home)
    expected_away = max(10.0, expected_away)
    over_under = round(expected_home + expected_away, 1)

    favoured = home_name if expected_margin >= 0 else away_name
    lead_prob = home_win_prob if expected_margin >= 0 else away_win_prob
    conf = _confidence(lead_prob)

    spread_label = (
        f"{home_name} -{abs(expected_margin):.1f}"
        if expected_margin > 0
        else f"{away_name} -{abs(expected_margin):.1f}"
    )
    best_bet = f"Victoria {favoured} ({lead_prob:.1f}%)"

    # ── Extended markets and player props ─────────────────────────────────────
    from core.props import nfl_quarter_projections, nfl_player_props
    quarters = nfl_quarter_projections(expected_home, expected_away)
    player_props = nfl_player_props(expected_home, expected_away)

    return {
        "sport": "NFL 🏈",
        "home": home_name,
        "away": away_name,
        "home_win": home_win_prob,
        "away_win": away_win_prob,
        "expected_home": expected_home,
        "expected_away": expected_away,
        "spread": round(expected_margin, 1),
        "spread_str": spread_label,
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
        "home_win_pct": round(home_win_pct, 3),
        "away_win_pct": round(away_win_pct, 3),
        "quarter_projections": quarters,
        "player_props": player_props,
    }
