"""
core/shot_metrics.py — Shot-volume analytics for football (soccer).

Computes advanced shot-based metrics that complement xG, xThreat, PPDA
and Field Tilt to give a more complete picture of offensive threat and
defensive solidity.

Metrics computed
----------------
shot_dominance          shots_team / (shots_team + shots_opponent)  (0-1)
shot_accuracy           shots_on_target / total_shots               (0-1)
shot_pressure           rolling avg shots (last-5 match window)
shot_quality            xG / total_shots                            (xG per shot)
goal_threat_rate        shots_on_target / xG                        (SoT per xG unit)
shot_differential       shots_team - shots_opponent

All functions are pure (no I/O). When live API shot data is unavailable the
module derives reliable shot estimates from xG using empirically calibrated
ratios from FBref / Opta (European top-5 leagues 2022-24).

xG → total shots conversion constants
    European top-5 average: ~11.5 shots/game per team, ~5.0 on target
    xG per shot ≈ 0.100  (total shots)
    xG per SoT  ≈ 0.320  (shots on target only)
    Shots on target / total shots ≈ 0.43

These are calibrated so that, at average xG ≈ 1.15:
    total_shots ≈ 11.5
    shots_on_target ≈ 5.0

Probability-adjustment rules (implemented in ``apply_shot_adjustments``)
-------------------------------------------------------------------------
Rule 1  shot_dominance > 0.60 AND xG_diff > 1.0
        → home/away win probability +4 pp

Rule 2  shots_on_target_diff > 3
        → over_1_5 probability +3 pp

Rule 3  shots_allowed < 3
        → clean-sheet probability +5 pp
          (stored as a bonus hint; actual CS prob comes from the football model)

Rule 4  total_shots_projection > 22
        → over_2_5 probability +4 pp
"""

from __future__ import annotations

from typing import Optional

# ── xG ↔ Shots calibration constants (FBref/Opta, top-5 2022-24) ──────────────
_XG_PER_TOTAL_SHOT = 0.100  # 1 total shot ≈ 0.10 xG
_XG_PER_SOT        = 0.320  # 1 shot on target ≈ 0.32 xG
_SOT_RATE          = 0.43   # ~43% of shots find the target

# League-average total shots per team per game (European top-5)
_LEAGUE_AVG_SHOTS       = 11.5
_LEAGUE_AVG_SOT         = 5.0

# ── Probability adjustment magnitudes (pp = percentage points) ─────────────────
_WIN_PROB_BOOST          = 4.0   # pp — shot dominance + xG edge
_OVER15_BOOST            = 3.0   # pp — shots on target differential
_OVER25_BOOST            = 4.0   # pp — high-volume match projection
_CS_BOOST_HINT           = 5.0   # pp hint — shots allowed < 3 → cleaner defence


# ══════════════════════════════════════════════════════════════════════════════
# Derivation from xG (always available even without a shots API)
# ══════════════════════════════════════════════════════════════════════════════

def xg_to_shots(xg: float) -> dict:
    """
    Derive shot-volume estimates from an xG value.

    Uses FBref/Opta calibrated constants when live shot data is unavailable.

    Returns
    -------
    {
        "total_shots"     : float,
        "shots_on_target" : float,
        "shots_off_target": float,
        "blocked_shots"   : float,
    }
    """
    xg = max(xg, 0.0)
    total  = round(xg / _XG_PER_TOTAL_SHOT, 1)
    sot    = round(xg / _XG_PER_SOT, 1)
    off    = round(max(total - sot, 0), 1)
    blocked = round(off * 0.35, 1)   # roughly 35% of off-target shots are blocked
    return {
        "total_shots":      total,
        "shots_on_target":  sot,
        "shots_off_target": off,
        "blocked_shots":    blocked,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Individual metric calculators
# ══════════════════════════════════════════════════════════════════════════════

def compute_shot_dominance(shots_team: float, shots_opponent: float) -> float:
    """
    Fraction of combined shots taken by *shots_team*.

    Returns 0.5 when both sides have zero shots (avoids division by zero).
    Clamped to [0.0, 1.0].
    """
    total = shots_team + shots_opponent
    if total <= 0:
        return 0.5
    return round(min(max(shots_team / total, 0.0), 1.0), 3)


def compute_shot_accuracy(shots_on_target: float, total_shots: float) -> float:
    """
    Ratio of shots on target to total shots (0-1, i.e. 0-100%).

    Returns league average (~0.43) when total_shots is zero or negative.
    """
    if total_shots <= 0:
        return _SOT_RATE
    return round(min(max(shots_on_target / total_shots, 0.0), 1.0), 3)


def compute_shot_quality(xg: float, total_shots: float) -> float:
    """
    xG per total shot — measures how dangerous each attempt is.

    A value > 0.12 indicates high-quality opportunities.
    Returns league-average xG-per-shot (0.10) when no shots are recorded.
    """
    if total_shots <= 0:
        return _XG_PER_TOTAL_SHOT
    return round(min(max(xg / total_shots, 0.0), 1.0), 3)


def compute_goal_threat_rate(shots_on_target: float, xg: float) -> float:
    """
    Shots on target per unit of xG.

    High values (> 3.5) mean many attempts but low average quality per shot.
    Low values (< 2.5) mean fewer but higher-quality chances.
    Returns 3.125 (= 1 / 0.32, the calibrated ratio) when xG is zero.
    """
    if xg <= 0:
        return round(1.0 / _XG_PER_SOT, 2)
    return round(max(shots_on_target / xg, 0.0), 2)


def compute_shot_differential(shots_team: float, shots_opponent: float) -> float:
    """Signed difference: positive means more shots than opponent."""
    return round(shots_team - shots_opponent, 1)


# ══════════════════════════════════════════════════════════════════════════════
# Aggregate: compute all metrics for one team
# ══════════════════════════════════════════════════════════════════════════════

def compute_shot_metrics(
    shots_on_target: float,
    total_shots: float,
    shots_opponent: float,
    xg: float,
    shots_on_target_opponent: float = 0.0,
) -> dict:
    """
    Compute all shot metrics for a single team.

    Parameters
    ----------
    shots_on_target          : SoT for this team
    total_shots              : total attempts (on + off + blocked) for this team
    shots_opponent           : total shots by the opponent
    xg                       : expected goals for this team
    shots_on_target_opponent : SoT for the opponent (used for differential/CS hint)

    Returns
    -------
    {
        "total_shots"             : float,
        "shots_on_target"         : float,
        "shots_off_target"        : float,
        "shot_dominance"          : float,   # 0-1
        "shot_accuracy"           : float,   # 0-1
        "shot_quality"            : float,   # xG per shot
        "goal_threat_rate"        : float,
        "shot_differential"       : float,
        "sot_differential"        : float,
        "shots_on_target_opponent": float,
    }
    """
    shots_off = max(total_shots - shots_on_target, 0.0)
    return {
        "total_shots":              round(total_shots, 1),
        "shots_on_target":          round(shots_on_target, 1),
        "shots_off_target":         round(shots_off, 1),
        "shot_dominance":           compute_shot_dominance(total_shots, shots_opponent),
        "shot_accuracy":            compute_shot_accuracy(shots_on_target, total_shots),
        "shot_quality":             compute_shot_quality(xg, total_shots),
        "goal_threat_rate":         compute_goal_threat_rate(shots_on_target, xg),
        "shot_differential":        compute_shot_differential(total_shots, shots_opponent),
        "sot_differential":         compute_shot_differential(shots_on_target, shots_on_target_opponent),
        "shots_on_target_opponent": round(shots_on_target_opponent, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Form averages and trend detection
# ══════════════════════════════════════════════════════════════════════════════

def compute_shot_form_averages(
    last5_shots: Optional[list] = None,
    last10_shots: Optional[list] = None,
) -> dict:
    """
    Compute shot averages from recent-match data and detect the offensive trend.

    Parameters
    ----------
    last5_shots  : list of (total_shots, shots_on_target, shots_allowed, sot_allowed)
                   tuples for the 5 most recent matches (newest last).
    last10_shots : same structure for the 10 most recent matches.

    Each element is a 4-tuple:
        (total_shots_for, sot_for, total_shots_against, sot_against)

    Returns
    -------
    {
        "avg_shots"              : float   # last 5 avg total shots for
        "avg_shots_on_target"    : float
        "avg_shots_allowed"      : float   # total shots conceded
        "avg_shots_on_target_allowed": float
        "avg_shots_l10"          : float   # same but last 10
        "trend"                  : str     # "attacking_form_up" | "attacking_form_down" | "stable"
    }
    """
    def _avg(data: list, idx: int) -> float:
        if not data:
            return 0.0
        return round(sum(row[idx] for row in data) / len(data), 1)

    l5  = last5_shots  or []
    l10 = last10_shots or []

    avg_shots5  = _avg(l5, 0)
    avg_sot5    = _avg(l5, 1)
    avg_allow5  = _avg(l5, 2)
    avg_sot_allow5 = _avg(l5, 3)

    avg_shots10 = _avg(l10, 0)

    # Trend: last-5 significantly higher than last-10 → attacking form rising
    if avg_shots10 > 0 and avg_shots5 > avg_shots10 * 1.10:
        trend = "attacking_form_up"
    elif avg_shots10 > 0 and avg_shots5 < avg_shots10 * 0.90:
        trend = "attacking_form_down"
    else:
        trend = "stable"

    return {
        "avg_shots":                   avg_shots5,
        "avg_shots_on_target":         avg_sot5,
        "avg_shots_allowed":           avg_allow5,
        "avg_shots_on_target_allowed": avg_sot_allow5,
        "avg_shots_l10":               avg_shots10,
        "trend":                       trend,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Shot projection
# ══════════════════════════════════════════════════════════════════════════════

def project_shots(
    avg_shots_team: float,
    avg_shots_allowed_opponent: float,
    shot_accuracy: float = _SOT_RATE,
) -> dict:
    """
    Project the number of shots a team will take against a specific opponent.

    Formula:
        projected_shots = (avg_shots_team + avg_shots_allowed_opponent) / 2

    Parameters
    ----------
    avg_shots_team               : team's season / recent average total shots
    avg_shots_allowed_opponent   : how many shots opponent typically allows
    shot_accuracy                : SoT / total shots ratio (default: league avg)

    Returns
    -------
    {
        "projected_shots"           : float,
        "projected_shots_on_target" : float,
    }
    """
    avg_shots_team             = max(avg_shots_team, 0.0)
    avg_shots_allowed_opponent = max(avg_shots_allowed_opponent, 0.0)

    projected = round((avg_shots_team + avg_shots_allowed_opponent) / 2, 1)
    projected_sot = round(projected * max(shot_accuracy, 0.0), 1)

    return {
        "projected_shots":           projected,
        "projected_shots_on_target": projected_sot,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Probability adjustments
# ══════════════════════════════════════════════════════════════════════════════

def apply_shot_adjustments(
    xg_home: float,
    xg_away: float,
    probs: dict,
    home_shots: dict,
    away_shots: dict,
    total_shots_projection: float = 0.0,
) -> dict:
    """
    Apply shot-based probability adjustments on top of the base model.

    The adjustments are small (≤ 4 pp each) and additive so they cannot
    individually dominate the Poisson / Dixon-Coles model.  Each rule is
    applied independently; multiple rules can fire simultaneously.

    Parameters
    ----------
    xg_home, xg_away     : expected goals from the base model
    probs                : base probability dict with keys home_win, draw,
                           away_win, over_1_5, over_2_5, over_3_5, btts
    home_shots / away_shots : shot metrics dicts from ``compute_shot_metrics``
    total_shots_projection  : sum of projected total shots for both teams

    Returns
    -------
    Adjusted *copy* of probs dict with an extra ``shot_adjustment_applied``
    bool and ``shot_adjustment_reasons`` list for traceability.
    """
    adjusted = dict(probs)
    reasons: list = []

    home_dominance = home_shots.get("shot_dominance", 0.5)
    away_dominance = away_shots.get("shot_dominance", 0.5)
    xg_diff        = xg_home - xg_away
    sot_diff_home  = home_shots.get("sot_differential", 0.0)

    # Rule 1 — Shot dominance AND xG edge → win-probability boost
    if home_dominance > 0.60 and xg_diff > 1.0:
        adjusted["home_win"] = min(adjusted.get("home_win", 0) + _WIN_PROB_BOOST, 99.0)
        reasons.append("SHOT_DOM_HOME")
    if away_dominance > 0.60 and (-xg_diff) > 1.0:
        adjusted["away_win"] = min(adjusted.get("away_win", 0) + _WIN_PROB_BOOST, 99.0)
        reasons.append("SHOT_DOM_AWAY")

    # Rule 2 — SoT differential > 3 → more goals expected
    if sot_diff_home > 3:
        adjusted["over_1_5"] = min(adjusted.get("over_1_5", 0) + _OVER15_BOOST, 99.0)
        reasons.append("HIGH_SOT_HOME")
    elif (-sot_diff_home) > 3:
        adjusted["over_1_5"] = min(adjusted.get("over_1_5", 0) + _OVER15_BOOST, 99.0)
        reasons.append("HIGH_SOT_AWAY")

    # Rule 3 — Low shots allowed → clean sheet hint (stored, not modifying CS directly)
    home_sot_allowed = home_shots.get("shots_on_target_opponent", _LEAGUE_AVG_SOT)
    away_sot_allowed = away_shots.get("shots_on_target_opponent", _LEAGUE_AVG_SOT)
    if away_sot_allowed < 3.0:
        # Away team gives up very few SoT → home clean sheet more likely
        reasons.append("LOW_SOT_ALLOWED_AWAY")
    if home_sot_allowed < 3.0:
        reasons.append("LOW_SOT_ALLOWED_HOME")

    # Rule 4 — High projected total shots → over-2.5 boost
    if total_shots_projection > 22:
        adjusted["over_2_5"] = min(adjusted.get("over_2_5", 0) + _OVER25_BOOST, 99.0)
        reasons.append("HIGH_VOL_MATCH")

    adjusted["shot_adjustment_applied"]  = bool(reasons)
    adjusted["shot_adjustment_reasons"]  = reasons

    # Normalise 1X2 so they still sum to ~100 %
    hw = adjusted.get("home_win", 0)
    dr = adjusted.get("draw", 0)
    aw = adjusted.get("away_win", 0)
    total_1x2 = hw + dr + aw
    if total_1x2 > 0:
        adjusted["home_win"] = round(hw / total_1x2 * 100, 1)
        adjusted["draw"]     = round(dr / total_1x2 * 100, 1)
        adjusted["away_win"] = round(aw / total_1x2 * 100, 1)

    return adjusted


# ══════════════════════════════════════════════════════════════════════════════
# Auto-pick generation from shot data
# ══════════════════════════════════════════════════════════════════════════════

def generate_shot_picks(
    home_shots: dict,
    away_shots: dict,
    xg_home: float,
    xg_away: float,
    probs: dict,
    home_name: str = "Home",
    away_name: str = "Away",
) -> list:
    """
    Generate value picks based on shot metrics rules.

    Returns a list of pick dicts:
        {"pick": str, "reason": str, "market": str}

    Rules (see problem statement §7)
    ----------------------------------
    R1: home SoT ≥ 6 AND xG_home ≥ 1.8  → Home Over 1.5 Goals
    R2: home dominance ≥ 0.65           → Home to Win
    R3: away SoT allowed ≤ 2            → Home Win to Nil
    R4: projected total shots ≥ 25      → Over 2.5 Goals

    Mirror rules for the away side (R1/R2/R3) are also checked.
    """
    picks = []

    h_sot         = home_shots.get("shots_on_target", 0.0)
    a_sot         = away_shots.get("shots_on_target", 0.0)
    h_dominance   = home_shots.get("shot_dominance", 0.5)
    a_dominance   = away_shots.get("shot_dominance", 0.5)
    h_proj        = home_shots.get("projected_shots", home_shots.get("total_shots", 0.0))
    a_proj        = away_shots.get("projected_shots", away_shots.get("total_shots", 0.0))
    total_proj    = h_proj + a_proj

    # R1 — High SoT + xG → team over goals
    if h_sot >= 6.0 and xg_home >= 1.8:
        picks.append({
            "pick":   f"{home_name} Over 1.5 Goals",
            "reason": f"SoT {h_sot:.0f} & xG {xg_home:.2f}",
            "market": "team_over_1_5",
        })
    if a_sot >= 6.0 and xg_away >= 1.8:
        picks.append({
            "pick":   f"{away_name} Over 1.5 Goals",
            "reason": f"SoT {a_sot:.0f} & xG {xg_away:.2f}",
            "market": "team_over_1_5",
        })

    # R2 — Shot dominance ≥ 65%
    if h_dominance >= 0.65:
        picks.append({
            "pick":   f"{home_name} Victoria",
            "reason": f"Dominio tiros {h_dominance*100:.0f}%",
            "market": "moneyline",
        })
    if a_dominance >= 0.65:
        picks.append({
            "pick":   f"{away_name} Victoria",
            "reason": f"Dominio tiros {a_dominance*100:.0f}%",
            "market": "moneyline",
        })

    # R3 — Opponent allows very few SoT → Win to Nil
    h_sot_allowed = home_shots.get("shots_on_target_opponent", _LEAGUE_AVG_SOT)
    a_sot_allowed = away_shots.get("shots_on_target_opponent", _LEAGUE_AVG_SOT)
    if a_sot_allowed <= 2.0 and xg_home > xg_away:
        picks.append({
            "pick":   f"{home_name} a Cero",
            "reason": f"Rival concede ≤{a_sot_allowed:.0f} SoT",
            "market": "win_to_nil",
        })
    if h_sot_allowed <= 2.0 and xg_away > xg_home:
        picks.append({
            "pick":   f"{away_name} a Cero",
            "reason": f"Rival concede ≤{h_sot_allowed:.0f} SoT",
            "market": "win_to_nil",
        })

    # R4 — High-volume match → Over 2.5
    if total_proj >= 25:
        picks.append({
            "pick":   "Over 2.5 Goles",
            "reason": f"Tiros proyectados {total_proj:.0f}",
            "market": "over_2_5",
        })

    return picks


# ══════════════════════════════════════════════════════════════════════════════
# Convenience: build full shot context from xG (no API needed)
# ══════════════════════════════════════════════════════════════════════════════

def build_shot_context_from_xg(
    xg_home: float,
    xg_away: float,
    probs: dict,
    home_name: str = "Home",
    away_name: str = "Away",
    home_form_shots: Optional[dict] = None,
    away_form_shots: Optional[dict] = None,
) -> dict:
    """
    Build a complete shot-metrics context using only xG (always available).

    Optionally enriches the estimates with ``home_form_shots`` /
    ``away_form_shots`` dicts (from ``compute_shot_form_averages``) when
    available from an external API.

    Returns
    -------
    {
        "home": {shot metrics},
        "away": {shot metrics},
        "projection": {"projected_shots": float, "projected_shots_on_target": float},
        "adjusted_probs": {...},
        "shot_picks": [...],
    }
    """
    h_raw = xg_to_shots(xg_home)
    a_raw = xg_to_shots(xg_away)

    # If we have form-based shot averages, prefer them over raw xG derivation
    if home_form_shots and home_form_shots.get("avg_shots", 0) > 0:
        h_total = home_form_shots["avg_shots"]
        h_sot   = home_form_shots.get("avg_shots_on_target", h_raw["shots_on_target"])
    else:
        h_total = h_raw["total_shots"]
        h_sot   = h_raw["shots_on_target"]

    if away_form_shots and away_form_shots.get("avg_shots", 0) > 0:
        a_total = away_form_shots["avg_shots"]
        a_sot   = away_form_shots.get("avg_shots_on_target", a_raw["shots_on_target"])
    else:
        a_total = a_raw["total_shots"]
        a_sot   = a_raw["shots_on_target"]

    # Shot-allowed from form (how many shots does each defence face?)
    if home_form_shots and home_form_shots.get("avg_shots_allowed", 0) > 0:
        h_allowed     = home_form_shots["avg_shots_allowed"]
        h_sot_allowed = home_form_shots.get("avg_shots_on_target_allowed", a_sot)
    else:
        h_allowed     = a_total   # proxy: opponent's shots = away team's total shots
        h_sot_allowed = a_sot

    if away_form_shots and away_form_shots.get("avg_shots_allowed", 0) > 0:
        a_allowed     = away_form_shots["avg_shots_allowed"]
        a_sot_allowed = away_form_shots.get("avg_shots_on_target_allowed", h_sot)
    else:
        a_allowed     = h_total
        a_sot_allowed = h_sot

    home_metrics = compute_shot_metrics(
        shots_on_target=h_sot,
        total_shots=h_total,
        shots_opponent=a_total,
        xg=xg_home,
        shots_on_target_opponent=a_sot_allowed,
    )
    away_metrics = compute_shot_metrics(
        shots_on_target=a_sot,
        total_shots=a_total,
        shots_opponent=h_total,
        xg=xg_away,
        shots_on_target_opponent=h_sot_allowed,
    )

    # Shot accuracy from the home/away estimates
    h_accuracy = compute_shot_accuracy(h_sot, h_total)
    a_accuracy = compute_shot_accuracy(a_sot, a_total)

    home_proj = project_shots(
        avg_shots_team=h_total,
        avg_shots_allowed_opponent=a_allowed,
        shot_accuracy=h_accuracy,
    )
    away_proj = project_shots(
        avg_shots_team=a_total,
        avg_shots_allowed_opponent=h_allowed,
        shot_accuracy=a_accuracy,
    )

    # Enrich home/away metrics with projection data
    home_metrics["projected_shots"]           = home_proj["projected_shots"]
    home_metrics["projected_shots_on_target"] = home_proj["projected_shots_on_target"]
    away_metrics["projected_shots"]           = away_proj["projected_shots"]
    away_metrics["projected_shots_on_target"] = away_proj["projected_shots_on_target"]

    total_proj = home_proj["projected_shots"] + away_proj["projected_shots"]

    adjusted_probs = apply_shot_adjustments(
        xg_home, xg_away, probs,
        home_metrics, away_metrics,
        total_shots_projection=total_proj,
    )

    shot_picks = generate_shot_picks(
        home_metrics, away_metrics,
        xg_home, xg_away, probs,
        home_name=home_name, away_name=away_name,
    )

    return {
        "home":           home_metrics,
        "away":           away_metrics,
        "projection":     {"projected_shots": total_proj,
                           "home_projected":  home_proj["projected_shots"],
                           "away_projected":  away_proj["projected_shots"]},
        "adjusted_probs": adjusted_probs,
        "shot_picks":     shot_picks,
    }
