"""
core/props.py — Sport-specific prop and market projection models.

Provides statistical estimations for:
  Football  : shots on target per team, yellow/red card split per team
  NBA       : quarter-by-quarter score projections, player props (pts/reb/ast)
  MLB       : team hits, player props (hits, home runs, strikeouts for ace)
  NFL       : quarter-by-quarter score projections, player props
              (QB passing yards/TDs/completions, RB rushing yards/TDs,
               WR receptions/yards)

All functions are pure (no I/O) and return dicts/lists with rounded floats
so they can be serialised directly into bot messages.

Calibration sources:
  Football : FBref / Opta, European top-5 leagues 2022–24 average
  NBA      : Basketball-Reference, 2023-24 regular season
  MLB      : Baseball-Reference, 2023 season averages
  NFL      : Pro-Football-Reference, 2023 regular season
"""

from __future__ import annotations

import math


# ═══════════════════════════════════════════════════════════════════════════════
# FOOTBALL / SOCCER
# ═══════════════════════════════════════════════════════════════════════════════

# Each shot on target has roughly a 0.32 xG value in modern shot models.
_SOT_XG_RATIO = 0.32   # xG per SoT; lower bound-clamped to avoid division issues

# Yellow-card baseline per team per game (European top-5 2022-24)
_YELLOW_BASE = 1.5
# Each unit of opponent xG adds defensive pressure → more bookings
_YELLOW_PRESSURE_COEF = 0.12
# Red card rate (team level, per 90 min) — rare event
_RED_BASE = 0.075      # ~one red per 13 games per team


def football_shots_on_target(xg_home: float, xg_away: float) -> dict:
    """
    Estimate shots on target (SoT) per team using the xG/SoT ratio.

    Parameters
    ----------
    xg_home, xg_away : expected goals for home/away team (from the DC model).

    Returns
    -------
    {
        "sot_home"  : float,   # expected SoT for the home team
        "sot_away"  : float,   # expected SoT for the away team
        "sot_total" : float,   # combined
        "line"      : float,   # total SoT line (rounded to nearest 0.5)
        "suggestion": str,     # "Over" / "Under" relative to the line
    }
    """
    ratio = max(_SOT_XG_RATIO, 0.10)
    sot_home = round(xg_home / ratio, 1)
    sot_away = round(xg_away / ratio, 1)
    sot_total = round(sot_home + sot_away, 1)

    # Round to the nearest 0.5 for a conventional market line
    line = round(round(sot_total * 2) / 2, 1)
    suggestion = "Over" if sot_total >= line else "Under"

    return {
        "sot_home": sot_home,
        "sot_away": sot_away,
        "sot_total": sot_total,
        "line": line,
        "suggestion": suggestion,
    }


def football_cards_detail(xg_home: float, xg_away: float) -> dict:
    """
    Estimate yellow and red card distribution per team.

    The defending team accumulates yellows proportional to the attacking
    pressure of the opponent (more xG → more fouls needed → more yellows).

    Returns
    -------
    {
        "yellow_home"   : float,
        "yellow_away"   : float,
        "total_yellow"  : float,
        "total_red"     : float,
        "total_cards"   : float,   # yellow + red combined
        "over_3_5_cards": bool,    # projected > 3.5 total cards
        "over_4_5_cards": bool,    # projected > 4.5 total cards
    }
    """
    # Away attack → home team must defend → home team fouls → home yellow cards
    yellow_home = round(_YELLOW_BASE + xg_away * _YELLOW_PRESSURE_COEF, 1)
    yellow_away = round(_YELLOW_BASE + xg_home * _YELLOW_PRESSURE_COEF, 1)
    total_yellow = round(yellow_home + yellow_away, 1)

    # Red cards scale slightly with game intensity (total xG proxy)
    intensity = xg_home + xg_away
    total_red = round(2 * _RED_BASE + intensity * 0.02, 2)

    total_cards = round(total_yellow + total_red, 1)

    return {
        "yellow_home": yellow_home,
        "yellow_away": yellow_away,
        "total_yellow": total_yellow,
        "total_red": round(total_red, 2),
        "total_cards": total_cards,
        "over_3_5_cards": total_cards > 3.5,
        "over_4_5_cards": total_cards > 4.5,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# NBA — BASKETBALL
# ═══════════════════════════════════════════════════════════════════════════════

# Empirical NBA quarter-scoring share of game total (2023-24).
# Q4 is highest due to intentional fouling, late free throws, and game flow.
_NBA_Q_FACTORS = (0.245, 0.250, 0.237, 0.268)   # must sum to 1.0

# Typical starter role shares of team PPG (based on positional archetypes)
# [ball-handler/PG, wing/SG-SF, forward/PF, center/C, bench]
_NBA_POS_SHARES = {
    "star_pts_pct": 0.25,
    "2nd_scorer_pct": 0.19,
    "playmaker_ast_avg": 7.2,    # per 36 min, scaled from NBA avg
    "big_reb_avg": 10.5,         # per 36 min for starting C
    "wing_reb_avg": 5.2,
    "role_pts_pct": 0.13,
}

# League average assists per PPG (used to scale assist projections)
_NBA_AVG_PPG = 112.5
_NBA_AVG_AST_PG = 26.0    # avg assists per team per game


def nba_quarter_projections(expected_home: float, expected_away: float) -> list:
    """
    Project expected points scored per quarter for each team.

    Parameters
    ----------
    expected_home, expected_away : projected final scores from the model.

    Returns
    -------
    List of 4 dicts:
      [{"quarter": 1, "home": float, "away": float, "total": float}, ...]
    """
    quarters = []
    for i, qf in enumerate(_NBA_Q_FACTORS):
        home_q = round(expected_home * qf, 1)
        away_q = round(expected_away * qf, 1)
        quarters.append(
            {
                "quarter": i + 1,
                "home": home_q,
                "away": away_q,
                "total": round(home_q + away_q, 1),
            }
        )
    return quarters


def nba_player_props(
    home_ppg: float,
    away_ppg: float,
    home_name: str = "Local",
    away_name: str = "Visitante",
    home_reb: float = 0.0,
    away_reb: float = 0.0,
    home_ast: float = 0.0,
    away_ast: float = 0.0,
) -> dict:
    """
    Estimate player prop lines based on team PPG and positional archetypes.

    No external player database is required — props are scaled from the team's
    projected scoring pace, giving realistic over/under lines for generic
    "starter" roles.

    Returns
    -------
    {
        "home": {
            "star_points": float,     # top scorer O/U line
            "2nd_scorer": float,
            "role_player": float,
            "assists": float,         # primary ball-handler assists
            "rebounds_big": float,    # starting centre rebounds
            "rebounds_wing": float,
            "team_rebounds": float,
            "team_assists": float,
        },
        "away": { … same keys … }
    }
    """

    # NBA league averages used when live data is unavailable
    _NBA_REB_AVG        = 44.0   # total team rebounds per game
    _NBA_AST_AVG        = 26.0   # total team assists per game
    _NBA_REB_BIG        = 0.24   # share of rebounds by starting center
    _NBA_REB_WING       = 0.12   # share of rebounds by wing player
    _NBA_PG_AST_SHARE   = 0.42   # share of team assists belonging to primary ball-handler

    def _team_props(ppg: float, reb_pg: float, ast_pg: float) -> dict:
        # Use live data when available; fall back to league-average scaling
        reb_base  = reb_pg  if reb_pg  > 0 else _NBA_REB_AVG  * (ppg / _NBA_AVG_PPG)
        ast_base  = ast_pg  if ast_pg  > 0 else _NBA_AST_AVG  * (ppg / _NBA_AVG_PPG)
        return {
            "star_points":    round(ppg * _NBA_POS_SHARES["star_pts_pct"],   1),
            "2nd_scorer":     round(ppg * _NBA_POS_SHARES["2nd_scorer_pct"], 1),
            "role_player":    round(ppg * _NBA_POS_SHARES["role_pts_pct"],   1),
            "assists":        round(ast_base * _NBA_PG_AST_SHARE, 1),
            "rebounds_big":   round(reb_base * _NBA_REB_BIG,  1),
            "rebounds_wing":  round(reb_base * _NBA_REB_WING, 1),
            "team_rebounds":  round(reb_base, 1),
            "team_assists":   round(ast_base, 1),
        }

    return {
        "home": _team_props(home_ppg, home_reb, home_ast),
        "away": _team_props(away_ppg, away_reb, away_ast),
    }


def nba_game_totals(expected_home: float, expected_away: float) -> dict:
    """
    Compute team and game total market lines for NBA.

    Returns
    -------
    {
        "team_total_home": float,
        "team_total_away": float,
        "game_total"     : float,
        "over_under_line": float,   # nearest whole number
        "team_h_over"    : float,   # suggested home team total line
        "team_a_over"    : float,
    }
    """
    game_total = round(expected_home + expected_away, 1)
    # Conventional line = nearest whole number
    ou_line = round(game_total)
    return {
        "team_total_home": expected_home,
        "team_total_away": expected_away,
        "game_total": game_total,
        "over_under_line": float(ou_line),
        "team_h_over": round(expected_home),
        "team_a_over": round(expected_away),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MLB — BASEBALL
# ═══════════════════════════════════════════════════════════════════════════════

# MLB league averages (2023 regular season)
_MLB_AVG_RPG = 4.5
_MLB_TEAM_HITS_AVG = 8.5       # hits per game per team
_MLB_CLEANUP_HITS = 1.0        # cleanup hitter hits per game
_MLB_CLEANUP_HR = 0.10         # cleanup hitter HR per game
_MLB_ACE_K_PER_9 = 8.5        # strikeouts per 9 innings for ace
_MLB_ACE_IP = 6.0              # innings pitched per start for ace


def mlb_player_props(xr_home: float, xr_away: float) -> dict:
    """
    Estimate player prop lines for a standard MLB lineup.

    Scales from the expected-runs model to derive:
    - Team total hits
    - Cleanup hitter hits / HR
    - Starting pitcher strikeouts

    Parameters
    ----------
    xr_home, xr_away : expected runs per team (from the xG model).

    Returns
    -------
    {
        "home": {
            "team_hits": float,
            "cleanup_hits": float,
            "cleanup_hr"  : float,
            "ace_strikeouts": float,   # home ace pitching vs away lineup
        },
        "away": { … }
    }
    """
    def _team_batting(xr: float) -> dict:
        scale = xr / _MLB_AVG_RPG
        return {
            "team_hits": round(_MLB_TEAM_HITS_AVG * scale, 1),
            "cleanup_hits": round(_MLB_CLEANUP_HITS * scale, 2),
            "cleanup_hr": round(_MLB_CLEANUP_HR * scale, 3),
        }

    def _ace_ks(opp_xr: float) -> float:
        """Pitcher strikeouts: ace K/9 scaled by opponent's offensive level."""
        # Better opponent offense (higher xR) → fewer Ks per inning for the ace
        opp_scale = _MLB_AVG_RPG / max(opp_xr, 0.5)
        ks = _MLB_ACE_K_PER_9 * opp_scale * (_MLB_ACE_IP / 9.0)
        return round(max(ks, 2.0), 1)

    home_batting = _team_batting(xr_home)
    away_batting = _team_batting(xr_away)
    home_batting["ace_strikeouts"] = _ace_ks(xr_away)   # home pitcher vs away batters
    away_batting["ace_strikeouts"] = _ace_ks(xr_home)   # away pitcher vs home batters

    return {"home": home_batting, "away": away_batting}


def mlb_run_line(xr_home: float, xr_away: float) -> dict:
    """
    MLB run-line (equivalent to spread): favourites at -1.5 runs.

    Returns
    -------
    {
        "run_line_fav": str,     # e.g. "Yankees -1.5"
        "run_line_dog": str,     # e.g. "Red Sox +1.5"
        "fav_cover_prob": float, # % chance favourite wins by 2+
        "over_under": float,
    }
    """
    spread = xr_home - xr_away
    fav, dog = ("home", "away") if spread >= 0 else ("away", "home")
    fav_xr = xr_home if fav == "home" else xr_away
    dog_xr = xr_away if fav == "home" else xr_home

    # Probability of winning by 2+ using Poisson difference
    # P(fav - dog >= 2) ≈ sum over (hg-ag >= 2) from Poisson(fav_xr) x Poisson(dog_xr)
    from core.distributions import poisson_pmf
    cover_prob = 0.0
    for fav_g in range(15):
        for dog_g in range(15):
            if fav_g - dog_g >= 2:
                cover_prob += poisson_pmf(fav_g, fav_xr) * poisson_pmf(dog_g, dog_xr)

    return {
        "fav_side": fav,
        "cover_prob": round(cover_prob * 100, 1),
        "over_under": round(xr_home + xr_away, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# NFL — AMERICAN FOOTBALL
# ═══════════════════════════════════════════════════════════════════════════════

# Empirical NFL quarter-scoring share of game total (2023 season averages).
# Q2 and Q4 are highest (late-half TD drives and garbage time).
_NFL_Q_FACTORS = (0.238, 0.262, 0.226, 0.274)   # must sum to 1.0

# NFL 2023 season averages for player props (per game, per team)
_NFL_AVG_PPG = 22.0
_NFL_QB_PASS_YARDS = 250.0    # passing yards per game (all QBs)
_NFL_QB_PASS_TDS = 1.65       # passing TDs per game
_NFL_QB_COMPLETIONS = 23.0    # completions per game
_NFL_RB_RUSH_YARDS = 108.0    # rushing yards per game (full backfield)
_NFL_RB_RUSH_TDS = 0.72       # rushing TDs per game
_NFL_RB_RECEPTIONS = 4.2      # RB receptions per game
_NFL_WR1_RECV_YARDS = 78.0    # WR1 receiving yards per game
_NFL_WR1_RECEPTIONS = 5.4     # WR1 receptions per game
_NFL_WR1_RECV_TDS = 0.35      # WR1 receiving TDs per game


def nfl_quarter_projections(expected_home: float, expected_away: float) -> list:
    """
    Project expected points scored per quarter for each team.

    Returns
    -------
    List of 4 dicts:
      [{"quarter": 1, "home": float, "away": float, "total": float}, ...]
    """
    quarters = []
    for i, qf in enumerate(_NFL_Q_FACTORS):
        home_q = round(expected_home * qf, 1)
        away_q = round(expected_away * qf, 1)
        quarters.append(
            {
                "quarter": i + 1,
                "home": home_q,
                "away": away_q,
                "total": round(home_q + away_q, 1),
            }
        )
    return quarters


def nfl_player_props(
    expected_home: float,
    expected_away: float,
) -> dict:
    """
    Estimate NFL player prop lines scaled from the projected game score.

    Props are estimated for the typical starter archetype at each position.
    They scale proportionally from the team's offensive output relative to the
    NFL average PPG (22.0 for 2023 season).

    Returns
    -------
    {
        "home": {
            "qb_pass_yards"    : float,
            "qb_pass_tds"      : float,
            "qb_completions"   : float,
            "rb_rush_yards"    : float,
            "rb_rush_tds"      : float,
            "rb_receptions"    : float,
            "wr1_recv_yards"   : float,
            "wr1_receptions"   : float,
            "wr1_recv_tds"     : float,
        },
        "away": { … same keys … }
    }
    """
    def _team_props(xpts: float) -> dict:
        scale = xpts / _NFL_AVG_PPG
        return {
            "qb_pass_yards": round(_NFL_QB_PASS_YARDS * scale, 0),
            "qb_pass_tds": round(_NFL_QB_PASS_TDS * scale, 1),
            "qb_completions": round(_NFL_QB_COMPLETIONS * scale, 0),
            "rb_rush_yards": round(_NFL_RB_RUSH_YARDS * scale, 0),
            "rb_rush_tds": round(_NFL_RB_RUSH_TDS * scale, 1),
            "rb_receptions": round(_NFL_RB_RECEPTIONS * scale, 1),
            "wr1_recv_yards": round(_NFL_WR1_RECV_YARDS * scale, 0),
            "wr1_receptions": round(_NFL_WR1_RECEPTIONS * scale, 1),
            "wr1_recv_tds": round(_NFL_WR1_RECV_TDS * scale, 1),
        }

    return {
        "home": _team_props(expected_home),
        "away": _team_props(expected_away),
    }
