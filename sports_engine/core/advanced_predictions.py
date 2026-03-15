"""
Advanced Predictions — Extended market models for Sports Engine.

Computes:
  - Draw No Bet (DNB): home/away probability excluding draws
  - Double Chance (1X, X2, 12): at least one of two 1X2 outcomes
  - Asian Handicap: configurable line, home/away/push probabilities
  - HT/FT combinations: top 6 most likely half-time/full-time combos
  - Team goal totals: home/away Over/Under 0.5, 1.5, 2.5
"""

import math
from typing import List, Dict

from core.distributions import poisson_pmf

_RHO = -0.13  # Dixon-Coles tau correction


def _dc_correction(hg: int, ag: int, xg_h: float, xg_a: float) -> float:
    """Dixon-Coles tau correction for low-scoring outcomes."""
    rho = _RHO
    if   hg == 0 and ag == 0: return 1.0 - xg_h * xg_a * rho
    elif hg == 1 and ag == 0: return 1.0 + xg_a * rho
    elif hg == 0 and ag == 1: return 1.0 + xg_h * rho
    elif hg == 1 and ag == 1: return 1.0 - rho
    return 1.0


def _build_score_matrix(
    xg_home: float,
    xg_away: float,
    max_goals: int = 8,
) -> Dict:
    """
    Build a normalised (hg, ag) → probability matrix using
    independent Poisson + Dixon-Coles correction.
    """
    matrix = {}
    for hg in range(max_goals + 1):
        for ag in range(max_goals + 1):
            p = poisson_pmf(hg, xg_home) * poisson_pmf(ag, xg_away)
            p *= _dc_correction(hg, ag, xg_home, xg_away)
            matrix[(hg, ag)] = p

    total = sum(matrix.values())
    if total > 0:
        matrix = {k: v / total for k, v in matrix.items()}
    return matrix


def compute_dnb(home_win: float, draw: float, away_win: float) -> Dict:
    """
    Draw No Bet — stake is refunded on a draw.

    DNB Home = home_win / (home_win + away_win)
    DNB Away = away_win / (home_win + away_win)
    """
    total = home_win + away_win
    if total <= 0:
        return {"dnb_home": 0.0, "dnb_away": 0.0}
    return {
        "dnb_home": round(home_win / total * 100, 1),
        "dnb_away": round(away_win / total * 100, 1),
    }


def compute_double_chance(home_win: float, draw: float, away_win: float) -> Dict:
    """
    Double Chance probabilities.
      1X  = home win or draw
      X2  = draw or away win
      12  = home win or away win  (no draw)
    """
    return {
        "dc_1x": round(home_win + draw,      1),
        "dc_x2": round(draw     + away_win,  1),
        "dc_12": round(home_win + away_win,  1),
    }


def compute_asian_handicap(
    xg_home: float,
    xg_away: float,
    line: float,
) -> Dict:
    """
    Asian Handicap for a given line (positive = home gives goals).

    For half-lines (.5): no push possible.
    For whole lines: push on exact handicap margin.

    Returns
    -------
    {"line": float, "home_prob": float, "away_prob": float, "push_prob": float}
    """
    matrix  = _build_score_matrix(xg_home, xg_away)
    is_half = (line % 1 != 0)

    home_covers = away_covers = push = 0.0

    for (hg, ag), p in matrix.items():
        margin = hg - ag
        if is_half:
            if margin > line:
                home_covers += p
            else:
                away_covers += p
        else:
            if margin > line:
                home_covers += p
            elif margin == line:
                push += p
            else:
                away_covers += p

    return {
        "line":      line,
        "home_prob": round(home_covers * 100, 1),
        "away_prob": round(away_covers * 100, 1),
        "push_prob": round(push        * 100, 1),
    }


def compute_ht_ft(xg_home: float, xg_away: float) -> List[Dict]:
    """
    Compute HT/FT combination probabilities.

    Uses HT xG ≈ 45% of full-time xG.
    Returns top 6 most likely HT/FT combinations as:
      [{"outcome": "1/1", "prob": 28.4}, ...]
    """
    ht_mult  = 0.45
    xg_ht_h  = xg_home * ht_mult
    xg_ht_a  = xg_away * ht_mult
    xg_2h_h  = xg_home * (1 - ht_mult)
    xg_2h_a  = xg_away * (1 - ht_mult)

    results  = {}
    max_g    = 5

    for hh in range(max_g + 1):
        for ha in range(max_g + 1):
            ht_p = poisson_pmf(hh, xg_ht_h) * poisson_pmf(ha, xg_ht_a)
            ht_outcome = "1" if hh > ha else ("X" if hh == ha else "2")

            for fh in range(hh, max_g + 1):
                for fa in range(ha, max_g + 1):
                    extra_h = fh - hh
                    extra_a = fa - ha
                    ft_p = (
                        poisson_pmf(extra_h, xg_2h_h)
                        * poisson_pmf(extra_a, xg_2h_a)
                    )
                    ft_outcome = "1" if fh > fa else ("X" if fh == fa else "2")
                    key = f"{ht_outcome}/{ft_outcome}"
                    results[key] = results.get(key, 0.0) + ht_p * ft_p

    total = sum(results.values())
    if total > 0:
        results = {k: v / total for k, v in results.items()}

    sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
    return [
        {"outcome": k, "prob": round(v * 100, 1)}
        for k, v in sorted_results[:6]
    ]


def compute_team_totals(xg_home: float, xg_away: float) -> Dict:
    """
    Compute team goal totals: Over/Under 0.5, 1.5, 2.5 per team.
    Uses marginal Poisson (independent of score correlation).
    """
    def _over(xg: float, line: float) -> float:
        under = sum(poisson_pmf(k, xg) for k in range(int(line) + 1))
        return round((1.0 - under) * 100, 1)

    return {
        "home_over_0_5": _over(xg_home, 0),
        "home_over_1_5": _over(xg_home, 1),
        "home_over_2_5": _over(xg_home, 2),
        "away_over_0_5": _over(xg_away, 0),
        "away_over_1_5": _over(xg_away, 1),
        "away_over_2_5": _over(xg_away, 2),
    }


def compute_all_advanced(
    xg_home: float,
    xg_away: float,
    prediction: Dict,
) -> Dict:
    """
    Compute all advanced market predictions.

    Parameters
    ----------
    xg_home, xg_away : expected goals
    prediction        : full prediction dict (for home_win / draw / away_win)

    Returns
    -------
    {
        "dnb":            dict,
        "double_chance":  dict,
        "asian_handicap": list of dicts  (lines -1.5, -1.0, -0.5, 0, +0.5, +1.0, +1.5),
        "ht_ft":          list of dicts  (top 6),
        "team_totals":    dict,
    }
    """
    hw = prediction.get("home_win", 0)
    dr = prediction.get("draw",     0)
    aw = prediction.get("away_win", 0)

    ah_lines  = [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5]
    ah_results = [compute_asian_handicap(xg_home, xg_away, l) for l in ah_lines]

    return {
        "dnb":            compute_dnb(hw, dr, aw),
        "double_chance":  compute_double_chance(hw, dr, aw),
        "asian_handicap": ah_results,
        "ht_ft":          compute_ht_ft(xg_home, xg_away),
        "team_totals":    compute_team_totals(xg_home, xg_away),
    }
