"""
Form analysis module for Sports Engine.

Provides:
  - Exponential-decay weighted attack/defense from recent match history
  - Current winning/losing streak detection with momentum multiplier
  - Head-to-head (H2H) adjustment factor
  - Poisson-based clean sheet probability
"""

import math
from typing import List, Dict, Optional


# ─────────────────────────────────────────────
# EXPONENTIAL DECAY FORM
# ─────────────────────────────────────────────

def decay_weighted_stats(
    match_history: List[Dict],
    last_n: int = 10,
    alpha: float = 0.82,
) -> Optional[Dict]:
    """
    Compute exponentially-decayed attack/defense from recent matches.

    Most recent game gets weight alpha^0 = 1.0, the game k matches
    ago gets weight alpha^k.  Weights are normalised so they sum to 1.

    Parameters
    ----------
    match_history : list of {"scored": int, "conceded": int, ...}
                    in chronological order (oldest first).
    last_n        : number of recent games to consider.
    alpha         : decay factor (0 < alpha < 1).  Higher = slower decay.

    Returns
    -------
    {"attack": float, "defense": float} or None if no history.
    """
    if not match_history:
        return None

    recent = match_history[-last_n:]
    n = len(recent)

    # raw_weights[i] = alpha^(n-1-i): oldest game gets alpha^(n-1), newest gets 1.0
    raw_weights = [alpha ** (n - 1 - i) for i in range(n)]
    total_w = sum(raw_weights)
    weights = [w / total_w for w in raw_weights]

    w_scored = sum(m["scored"] * w for m, w in zip(recent, weights))
    w_conceded = sum(m["conceded"] * w for m, w in zip(recent, weights))

    return {"attack": round(w_scored, 3), "defense": round(w_conceded, 3)}


# ─────────────────────────────────────────────
# STREAK DETECTION
# ─────────────────────────────────────────────

def current_streak(match_history: List[Dict]) -> Dict:
    """
    Detect the current result streak and compute a momentum multiplier.

    Win  streaks → +2.5% per consecutive win,  capped at +10%.
    Loss streaks → −2.0% per consecutive loss, capped at −8%.
    Draw streaks → no adjustment.

    Parameters
    ----------
    match_history : list of {"result": "W"/"D"/"L", ...} in chron order.

    Returns
    -------
    {
        "type"       : "W" | "D" | "L" | "none",
        "length"     : int,
        "multiplier" : float,   # apply to xG
        "last5"      : str,     # e.g. "WWDLW"
    }
    """
    if not match_history:
        return {"type": "none", "length": 0, "multiplier": 1.0, "last5": "-----"}

    # Work from most recent to oldest
    reversed_results = [m["result"] for m in reversed(match_history[-7:])]
    last5 = "".join(reversed_results[:5]).ljust(5, "-")

    streak_type = reversed_results[0]
    streak_len = 1
    for r in reversed_results[1:]:
        if r == streak_type:
            streak_len += 1
        else:
            break

    if streak_type == "W":
        mult = 1.0 + min(streak_len * 0.025, 0.10)
    elif streak_type == "L":
        mult = 1.0 - min(streak_len * 0.020, 0.08)
    else:
        mult = 1.0

    return {
        "type": streak_type,
        "length": streak_len,
        "multiplier": round(mult, 3),
        "last5": last5,
    }


def form_emoji(streak: Dict) -> str:
    """Return a single emoji summarising a team's current form."""
    t = streak.get("type", "none")
    n = streak.get("length", 0)
    if t == "W" and n >= 4:
        return "🔥"
    elif t == "W" and n >= 2:
        return "📈"
    elif t == "L" and n >= 4:
        return "❄️"
    elif t == "L" and n >= 2:
        return "📉"
    else:
        return "➡️"


# ─────────────────────────────────────────────
# HEAD-TO-HEAD FACTOR
# ─────────────────────────────────────────────

def h2h_adjustment(h2h_records: List[tuple]) -> float:
    """
    Return a small multiplier for the *home team* xG based on H2H history.

    Positive H2H dominance (home team wins more than expected) → slight boost.
    Negative H2H history → slight penalty.  Maximum effect: ±6%.

    Parameters
    ----------
    h2h_records : [(home_goals, away_goals), ...] where home = current home team.

    Returns
    -------
    float multiplier, in range [0.94, 1.06].
    """
    n = len(h2h_records)
    if n < 3:
        return 1.0  # not enough data

    home_wins = sum(1 for hg, ag in h2h_records if hg > ag)
    h2h_home_rate = home_wins / n

    # General home win baseline ≈ 45 %; scale deviation with dampening factor 0.15
    adjustment = 1.0 + (h2h_home_rate - 0.45) * 0.15
    return round(max(0.94, min(1.06, adjustment)), 3)


def h2h_summary(h2h_records: List[tuple]) -> Dict:
    """Return a summary dict for display purposes."""
    n = len(h2h_records)
    if n == 0:
        return {"total": 0, "home_wins": 0, "draws": 0, "away_wins": 0}
    hw = sum(1 for hg, ag in h2h_records if hg > ag)
    d = sum(1 for hg, ag in h2h_records if hg == ag)
    aw = n - hw - d
    avg_total = round(sum(hg + ag for hg, ag in h2h_records) / n, 1)
    return {
        "total": n,
        "home_wins": hw,
        "draws": d,
        "away_wins": aw,
        "avg_goals": avg_total,
    }


# ─────────────────────────────────────────────
# CLEAN SHEET PROBABILITY
# ─────────────────────────────────────────────

def clean_sheet_prob(avg_conceded: float) -> float:
    """
    Poisson probability of conceding 0 goals: P(X=0) = exp(-lambda).

    This gives the empirical clean sheet rate consistent with the xG model.
    """
    return round(math.exp(-max(avg_conceded, 0.01)), 3)
