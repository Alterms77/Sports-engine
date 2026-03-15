"""
Advanced football analytics metrics.

- xThreat (Expected Threat): estimates how dangerous each team's ball progression is.
  Derived from xG, attack/defense ratios, and form. Higher xT = more dangerous buildup.

- PPDA (Passes Per Defensive Action): estimates pressing intensity.
  Lower PPDA = more aggressive pressing (e.g., Liverpool ~7).
  Higher PPDA = passive/deep block (e.g., Atlético ~14).
  Derived from defense rating, form, and xG relationship.

- Field Tilt: percentage of attacking activity in the opponent's final third.
  Derived from xG ratio, attack ratings, and home/away context.
"""


def compute_xthreat(
    xg: float,
    attack_rating: float,
    defense_opp: float,
    league_avg: float,
) -> float:
    """
    Compute xThreat for one team.

    Formula: xT = xg * (attack_rating / league_avg) * 1.3
    Clamped between 0.5 and 5.0.
    """
    league_avg = max(league_avg, 0.01)
    xt = xg * (attack_rating / league_avg) * 1.3
    return round(min(max(xt, 0.5), 5.0), 2)


def compute_ppda(
    defense_rating: float,
    attack_opp: float,
    league_avg: float,
    is_home: bool,
) -> float:
    """
    Compute PPDA (Passes Per Defensive Action) for one team.

    Lower defense rating (fewer goals conceded) = lower PPDA = more pressing.
    Base formula: 6.0 + (defense_rating / league_avg) * 6.0
    Home teams press ~10% harder: multiply by 0.9 if is_home.
    Clamped between 5.0 and 18.0.
    """
    league_avg = max(league_avg, 0.01)
    base = 6.0 + (defense_rating / league_avg) * 6.0
    if is_home:
        base *= 0.9
    return round(min(max(base, 5.0), 18.0), 1)


def compute_field_tilt(
    xg_team: float,
    xg_opp: float,
    attack_rating: float,
    league_avg: float,
) -> float:
    """
    Compute Field Tilt: percentage of attacking play in the opponent's final third.

    Formula: tilt = (xg_team / (xg_team + xg_opp)) * 100 * (attack_rating / league_avg) ^ 0.3
    Clamped between 20.0 and 80.0.
    """
    league_avg = max(league_avg, 0.01)
    total_xg = xg_team + xg_opp
    if total_xg <= 0:
        total_xg = 0.01
    base_share = xg_team / total_xg
    attack_mod = (attack_rating / league_avg) ** 0.3
    tilt = base_share * 100 * attack_mod
    return round(min(max(tilt, 20.0), 80.0), 1)


def compute_advanced_metrics(
    xg_home: float,
    xg_away: float,
    home_stats: dict,
    away_stats: dict,
    league_avg: float,
    home_home_stats: dict = None,
    away_away_stats: dict = None,
) -> dict:
    """
    Compute all advanced metrics for a match.

    Uses home/away split stats when available, falls back to combined stats.

    Returns dict with keys:
        xt_home, xt_away,
        ppda_home, ppda_away,
        tilt_home, tilt_away
    """
    league_avg = max(league_avg, 0.01)

    # Resolve stats: prefer home/away split, fall back to combined
    h_stats = home_home_stats if home_home_stats else home_stats
    a_stats = away_away_stats if away_away_stats else away_stats

    h_attack = h_stats.get("attack", league_avg) if h_stats else league_avg
    h_defense = h_stats.get("defense", league_avg) if h_stats else league_avg
    a_attack = a_stats.get("attack", league_avg) if a_stats else league_avg
    a_defense = a_stats.get("defense", league_avg) if a_stats else league_avg

    xt_home = compute_xthreat(xg_home, h_attack, a_defense, league_avg)
    xt_away = compute_xthreat(xg_away, a_attack, h_defense, league_avg)

    ppda_home = compute_ppda(h_defense, a_attack, league_avg, is_home=True)
    ppda_away = compute_ppda(a_defense, h_attack, league_avg, is_home=False)

    tilt_home = compute_field_tilt(xg_home, xg_away, h_attack, league_avg)
    tilt_away = compute_field_tilt(xg_away, xg_home, a_attack, league_avg)

    return {
        "xt_home": xt_home,
        "xt_away": xt_away,
        "ppda_home": ppda_home,
        "ppda_away": ppda_away,
        "tilt_home": tilt_home,
        "tilt_away": tilt_away,
    }
