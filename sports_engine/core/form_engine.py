"""
Team Form Engine — Deep form analysis for Sports Engine.

Provides extended form metrics beyond the basic streak detection:
  - Home/away context-specific analysis (home games vs away games)
  - Scoring consistency (standard deviation of goals)
  - BTTS and Over/Under game rates
  - Clean sheet and failed-to-score rates
  - Form trend: improving / declining / steady
  - Points per game
  - Last 5 scorelines
"""

import math
from typing import List, Dict, Optional


def analyze_team_form(
    match_history: List[Dict],
    home_only: bool = False,
    away_only: bool = False,
    last_n: int = 10,
) -> Dict:
    """
    Deep form analysis for a team.

    Parameters
    ----------
    match_history : list of match dicts from load_match_history().
                    Each has: scored, conceded, result (W/D/L), is_home (bool)
    home_only     : only analyse home matches
    away_only     : only analyse away matches
    last_n        : number of recent matches to consider

    Returns
    -------
    dict with comprehensive form statistics.
    """
    if not match_history:
        return _empty_form()

    # Filter by home/away context
    filtered = match_history
    if home_only:
        filtered = [m for m in match_history if m.get("is_home", False)]
    elif away_only:
        filtered = [m for m in match_history if not m.get("is_home", True)]

    if not filtered:
        return _empty_form()

    recent = filtered[-last_n:]
    n = len(recent)

    wins   = sum(1 for m in recent if m["result"] == "W")
    draws  = sum(1 for m in recent if m["result"] == "D")
    losses = sum(1 for m in recent if m["result"] == "L")

    scored_list   = [m.get("scored",   0) for m in recent]
    conceded_list = [m.get("conceded", 0) for m in recent]

    total_scored   = sum(scored_list)
    total_conceded = sum(conceded_list)

    clean_sheets    = sum(1 for c in conceded_list if c == 0)
    failed_to_score = sum(1 for s in scored_list   if s == 0)

    btts_count = sum(
        1 for s, c in zip(scored_list, conceded_list) if s > 0 and c > 0
    )
    over_1_5 = sum(
        1 for s, c in zip(scored_list, conceded_list) if s + c > 1
    )
    over_2_5 = sum(
        1 for s, c in zip(scored_list, conceded_list) if s + c > 2
    )
    over_3_5 = sum(
        1 for s, c in zip(scored_list, conceded_list) if s + c > 3
    )

    avg_scored   = total_scored   / n
    avg_conceded = total_conceded / n

    # Scoring consistency: lower std dev = more predictable scorer
    consistency = 0.0
    if n > 1:
        variance    = sum((g - avg_scored) ** 2 for g in scored_list) / (n - 1)
        consistency = round(math.sqrt(variance), 2)

    # Form trend: compare avg goals last-3 vs prior-3
    trend = "steady"
    if n >= 6:
        recent3_avg = sum(scored_list[-3:]) / 3
        prev3_avg   = sum(scored_list[-6:-3]) / 3
        if recent3_avg > prev3_avg + 0.3:
            trend = "improving"
        elif recent3_avg < prev3_avg - 0.3:
            trend = "declining"

    # Last 5 results string (most recent first)
    last5_list = [m["result"] for m in reversed(recent[-5:])]
    last5 = "".join(last5_list[:5]).ljust(5, "-")

    # Last 5 scorelines (oldest first for readability)
    last5_scores = [
        f"{m.get('scored', 0)}-{m.get('conceded', 0)}"
        for m in recent[-5:]
    ]

    # Points per game
    points = wins * 3 + draws
    ppg    = round(points / n, 2)

    return {
        "games":               n,
        "wins":                wins,
        "draws":               draws,
        "losses":              losses,
        "points_per_game":     ppg,
        "goals_scored":        round(avg_scored,   2),
        "goals_conceded":      round(avg_conceded, 2),
        "clean_sheets":        clean_sheets,
        "clean_sheet_rate":    round(clean_sheets    / n * 100, 1),
        "failed_to_score":     failed_to_score,
        "fts_rate":            round(failed_to_score / n * 100, 1),
        "btts_count":          btts_count,
        "btts_rate":           round(btts_count / n * 100, 1),
        "over_1_5_count":      over_1_5,
        "over_2_5_count":      over_2_5,
        "over_3_5_count":      over_3_5,
        "over_1_5_rate":       round(over_1_5 / n * 100, 1),
        "over_2_5_rate":       round(over_2_5 / n * 100, 1),
        "over_3_5_rate":       round(over_3_5 / n * 100, 1),
        "form_trend":          trend,
        "scoring_consistency": consistency,
        "last5":               last5,
        "last5_scores":        last5_scores,
    }


def _empty_form() -> Dict:
    """Return a zero-filled form result when no history is available."""
    return {
        "games": 0, "wins": 0, "draws": 0, "losses": 0,
        "points_per_game": 0.0,
        "goals_scored": 0.0,   "goals_conceded": 0.0,
        "clean_sheets": 0,     "clean_sheet_rate": 0.0,
        "failed_to_score": 0,  "fts_rate": 0.0,
        "btts_count": 0,       "btts_rate": 0.0,
        "over_1_5_count": 0,   "over_2_5_count": 0,   "over_3_5_count": 0,
        "over_1_5_rate": 0.0,  "over_2_5_rate": 0.0,  "over_3_5_rate": 0.0,
        "form_trend": "steady", "scoring_consistency": 0.0,
        "last5": "-----",      "last5_scores": [],
    }


def form_engine_report(
    home_name: str,
    home_history: List[Dict],
    away_name: str,
    away_history: List[Dict],
) -> Dict:
    """
    Generate a complete form engine report for a matchup.

    Returns form analysis for both teams (all games, home-only, away-only).
    """
    return {
        "home_all":  analyze_team_form(home_history, last_n=10),
        "home_home": analyze_team_form(home_history, home_only=True, last_n=8),
        "away_all":  analyze_team_form(away_history, last_n=10),
        "away_away": analyze_team_form(away_history, away_only=True, last_n=8),
    }


def format_form_report(
    name: str,
    all_form: Dict,
    context_form: Dict,
    is_home: bool,
) -> str:
    """
    Format a team's form engine report for Telegram display.

    Parameters
    ----------
    name         : canonical team name
    all_form     : output of analyze_team_form (all games)
    context_form : output of analyze_team_form (home or away only)
    is_home      : True if home team (affects context label)
    """
    ctx_label = "🏠 Casa" if is_home else "✈️ Fuera"
    trend_emoji = {
        "improving": "📈",
        "declining": "📉",
        "steady":    "➡️",
    }.get(all_form.get("form_trend", "steady"), "➡️")

    scores_str = ", ".join(all_form.get("last5_scores", [])[-5:]) or "sin datos"

    n_all   = all_form.get("games", 0)
    n_ctx   = context_form.get("games", 0)

    if n_all == 0:
        return f"*{name}*\n  ⚠️ Sin historial disponible\n"

    lines = [
        f"*{name}*",
        f"  Últimos {n_all} partidos  `{all_form['wins']}G-{all_form['draws']}E-{all_form['losses']}P`"
        f"  PPG: `{all_form['points_per_game']}`  {trend_emoji} _{all_form['form_trend'].capitalize()}_",
        f"  xG marcados: `{all_form['goals_scored']}` | concedidos: `{all_form['goals_conceded']}`",
        f"  Resultados: _{scores_str}_",
        "",
    ]

    if n_ctx > 0:
        lines += [
            f"  {ctx_label} ({n_ctx} partidos)",
            f"  🔒 CS: `{context_form['clean_sheet_rate']}%`"
            f"  🚫 FTS: `{context_form['fts_rate']}%`"
            f"  ⚽⚽ BTTS: `{context_form['btts_rate']}%`",
            f"  Over 2.5: `{context_form['over_2_5_rate']}%`"
            f"  Over 3.5: `{context_form['over_3_5_rate']}%`",
            f"  Consistencia goleadora: `±{all_form['scoring_consistency']}`",
        ]

    return "\n".join(lines)
