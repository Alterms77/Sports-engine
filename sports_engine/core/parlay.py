"""
Parlay (Combinada) Generator — multi-sport edition with hit-rate optimisation.

Builds reliable multi-leg parlays from the day's matches (Soccer, NBA, NFL, MLB)
by:
1. Running predictions for all today's matches across sports.
2. Filtering to only ALTA confidence picks (configurable) above a minimum
   probability threshold.
3. Scoring each match for *sport-specific* risk and excluding high-risk games.
4. Applying a "clarity" criterion (separation between top-1 and top-2 outcome)
   to avoid coin-flip situations.
5. Selecting the best legs — without forcing variety in safe mode so the
   globally strongest picks are always chosen.
6. Returning full traceability: ``(legs, report, excluded)``.

Two modes
---------
Default (``safe_mode=False``) — the classic balanced mode:
  * market whitelist: all (moneyline, totals, btts, spread)
  * risk threshold: ``RISK_THRESHOLD_DEFAULT`` (0.35 on a 0-1 scale)
  * min_prob: 75 %
  * max_legs: 5

Safe mode (``safe_mode=True``) — maximum hit-rate:
  * market whitelist: moneyline / 1X2 only (no totals / BTTS / spread)
  * risk threshold: ``RISK_THRESHOLD_SAFE`` (0.25 on a 0-1 scale)
  * min_prob: 62 %
  * max_legs: 3
  * clarity filter: p_best ≥ MIN_PROB_SAFE_ABS and separation ≥ MIN_SEP_SAFE
  * draw excluded unless p_draw ≥ 0.40 and separation ≥ 0.12
  * calibrated probs clamped to [0.50, 0.90]
  * no variety cap (best 2-3 legs win regardless of sport)
"""

from __future__ import annotations

import math
from typing import Optional


def _md_escape(text: str) -> str:
    """Escape underscores so Telegram Markdown doesn't treat them as italic markers."""
    return str(text).replace("_", r"\_")


def _poisson_over(lam: float, line: float) -> float:
    """Return P(X > line) where X ~ Poisson(lam) as a percentage (0–100).

    Used to convert an expected-value projection (e.g., expected corners = 10.2)
    into a realistic over-line probability for corners, cards, and runs markets.
    """
    if lam <= 0:
        return 0.0
    k_max = int(math.floor(line))          # P(X > line) = P(X ≥ k_max + 1)
    exp_neg_lam = math.exp(-lam)
    cdf = 0.0
    term = exp_neg_lam
    for k in range(k_max + 1):
        if k > 0:
            term *= lam / k
        cdf += term
    return round(max(0.0, min(100.0, (1.0 - cdf) * 100)), 1)


def _ou_prob(projected: float, line: float, sigma_frac: float = 0.12) -> float:
    """Return P(total > line) using a Gaussian approximation as a percentage.

    Used for high-scoring sports (NBA, NFL) where totals are roughly normal.
    ``sigma_frac`` is the standard deviation as a fraction of the projected
    total (default 12 %).  Output is clamped to [35 %, 80 %] to avoid
    overconfident edge cases from the model.
    """
    if projected <= 0 or line <= 0:
        return 50.0
    sigma = max(projected * sigma_frac, 1.0)
    z = (projected - line) / sigma
    prob = 0.5 * math.erfc(-z / math.sqrt(2))
    return round(max(35.0, min(80.0, prob * 100)), 1)

_CONFIDENCE_RANK = {"ALTA": 2, "MEDIA": 1, "BAJA": 0}

# Maximum legs of the same market type allowed (default mode)
_MAX_SAME_MARKET = 2

# ── Risk thresholds (0-1 scale) ────────────────────────────────────────────────
RISK_THRESHOLD_DEFAULT = 0.40   # tightened from 0.45 to improve hit rate
RISK_THRESHOLD_SAFE    = 0.25   # /parlay_safe (more conservative)

# Keep legacy name for test compatibility
_HIGH_RISK_SCORE = RISK_THRESHOLD_DEFAULT

# ── Default parlay quality thresholds ────────────────────────────────────────
# These are used by generate_parlay_legs() default arguments and by bot.py.
# Raising these from their previous values (min_prob=65, MEDIA confidence)
# improves hit rate by excluding borderline picks.
MIN_PROB_DEFAULT    = 68.0    # minimum calibrated probability for default /parlay
MIN_CONF_DEFAULT    = "ALTA"  # minimum confidence for default /parlay

# ── Form-streak risk penalty ──────────────────────────────────────────────────
# When the model picks a team to win but that team is on a notable losing streak,
# the pick is higher-risk than the raw probability suggests.  These constants
# control the penalty added to the risk score.
_FORM_STREAK_LOSS_SHORT  = 3   # streak length triggering a mild penalty
_FORM_STREAK_LOSS_LONG   = 5   # streak length triggering a stronger penalty
_FORM_STREAK_PENALTY_MID = 0.10   # penalty for ≥3-game losing streak
_FORM_STREAK_PENALTY_HIGH = 0.18  # penalty for ≥5-game losing streak

# ── Overconfidence calibration risk boost ─────────────────────────────────────
# If a market type has historically been overconfident (calibration factor < this
# threshold), add a small risk penalty so the leg is held to a higher standard.
_CAL_OVERCONF_THRESHOLD = 0.85  # calibration factor below which we add risk
_CAL_OVERCONF_PENALTY   = 0.05  # extra risk for consistently overconfident markets

# ── Calibration floor ─────────────────────────────────────────────────────────
# Maximum downward adjustment allowed per calibration step (percentage points).
# Prevents a "death spiral" where bad results push calibration so far down that
# even good picks fall below min_prob.  See ``calibrate_prob_gated`` for usage.
_CAL_FLOOR_MAX_REDUCTION = 15.0

# ── Safe-mode clarity thresholds ─────────────────────────────────────────────
# Minimum absolute probability of the best outcome (safe mode)
MIN_PROB_SAFE_ABS = 58.0   # lowered from 62.0
# Minimum gap between best and second-best outcome (percentage points)
MIN_SEP_SAFE      = 8.0    # lowered from 12.0
# Draw allowed if its probability is at least this high
MIN_DRAW_PROB     = 40.0

# ── Market whitelists ─────────────────────────────────────────────────────────
_SAFE_MODE_MARKETS   = {"moneyline"}
_DEFAULT_MODE_MARKETS = {"moneyline", "totals", "btts", "spread"}

# ── Sport emoji lookup ────────────────────────────────────────────────────────
_SPORT_EMOJI = {
    "nba": "🏀",
    "nfl": "🏈",
    "mlb": "⚾",
    "soccer": "⚽",
    "football": "⚽",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf — no scipy required."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sport_emoji(pred: dict) -> str:
    """Return the appropriate sport emoji for a prediction dict."""
    raw = str(pred.get("sport", "")).lower()
    for key, emoji in _SPORT_EMOJI.items():
        if key in raw:
            return emoji
    return "⚽"


def _sport_key(pred: dict) -> str:
    """Return a normalised sport string ('soccer','nba','nfl','mlb','tennis')."""
    raw = str(pred.get("sport", "")).lower()
    for key in ("nba", "nfl", "mlb", "tennis"):
        if key in raw:
            return key
    return "soccer"


def _get_streak(pred: dict, side: str) -> dict:
    """
    Extract streak info for *side* ('home' or 'away') from a prediction dict.

    Soccer predictions include ``form_home`` / ``form_away`` dicts with a
    nested ``streak`` sub-dict (``{"type": "W"/"L"/"D", "length": int}``)
    populated by ``core.form.current_streak()``.

    For non-soccer sports the field is absent and we return an empty dict.
    """
    key = f"form_{side}"
    form = pred.get(key, {})
    if isinstance(form, dict):
        return form.get("streak", {}) or {}
    return {}


def _form_fade_penalty(pred: dict) -> tuple[float, list]:
    """
    Return an extra risk penalty when the model's moneyline pick contradicts
    the team's recent form (a "form fade" situation).

    Logic:
    - Determine which side the model favours (higher home_win vs away_win).
    - Read that side's current streak from ``form_home`` / ``form_away``.
    - If the favoured side is on a losing streak ≥ ``_FORM_STREAK_LOSS_SHORT``
      games, add a graduated penalty.
    - Also flag an opposing hot streak (≥3 W) as a secondary risk indicator.

    Returns (penalty: float, reasons: list[str])
    """
    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)

    if hw <= 0.0 and aw <= 0.0:
        return 0.0, []

    if hw >= aw:
        fav_side, opp_side = "home", "away"
    else:
        fav_side, opp_side = "away", "home"

    fav_streak = _get_streak(pred, fav_side)
    opp_streak = _get_streak(pred, opp_side)

    penalty = 0.0
    reasons: list = []

    fav_type   = fav_streak.get("type", "")
    fav_length = int(fav_streak.get("length", 0))

    # Penalise when the model's pick is on a sustained losing run
    if fav_type == "L":
        if fav_length >= _FORM_STREAK_LOSS_LONG:
            penalty += _FORM_STREAK_PENALTY_HIGH
            reasons.append("FORM_FADE")
        elif fav_length >= _FORM_STREAK_LOSS_SHORT:
            penalty += _FORM_STREAK_PENALTY_MID
            reasons.append("FORM_FADE")

    # Secondary: opponent on a hot winning streak — harder to beat
    opp_type   = opp_streak.get("type", "")
    opp_length = int(opp_streak.get("length", 0))
    if opp_type == "W" and opp_length >= _FORM_STREAK_LOSS_SHORT:
        penalty += _FORM_STREAK_PENALTY_MID * 0.5   # half-weight secondary signal
        if "FORM_FADE" not in reasons:
            reasons.append("FORM_FADE")

    return penalty, reasons


# ── Per-sport risk scoring ────────────────────────────────────────────────────

def score_risk_soccer(pred: dict) -> tuple:
    """
    Soccer-specific risk scoring.

    Returns (risk_score: float 0-1, reasons: list[str])

    Penalises:
      * Balanced/coin-flip moneyline (p_best < 55 %): HIGH_RISK
      * Moderately balanced (55-60 %): moderate penalty
      * BAJA confidence: always excluded
      * MEDIA confidence: small penalty
      * Sharp game detected: sharp money flag
      * No live data source: missing data flag
      * No market with strong probability (< 65 %): LOW_PROB
      * Very close draw probability (p_draw ≥ 30 % and < p_best + 10): COIN_FLIP
      * Favoured team on a recent losing streak: FORM_FADE

    Circumstantial "soft" penalties (SHARP, NO_LIVE_DATA, LOW_CONF, LOW_PROB,
    draw COIN_FLIP) are each capped at 0.25 to prevent over-stacking from
    turning a borderline pick into a hard reject.  The primary probability-
    quality checks (COIN_FLIP, LOW_SEPARATION) are not capped.
    """
    _SOFT_CAP = 0.25   # per-factor cap for circumstantial penalties

    risk = 0.0
    reasons: list = []

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    dr = float(pred.get("draw") or 0.0)
    top_side = max(hw, aw)

    # Primary quality checks — not capped (these are decisive)
    if top_side < 55.0:
        risk += 0.40
        reasons.append("COIN_FLIP")
    elif top_side < 60.0:
        risk += 0.20
        reasons.append("LOW_SEPARATION")

    conf = pred.get("confidence", "BAJA")
    if conf == "BAJA":
        return 1.0, ["LOW_CONF"]
    if conf == "MEDIA":
        risk += min(0.15, _SOFT_CAP)
        reasons.append("LOW_CONF")

    sharp = pred.get("sharp", {})
    if sharp and sharp.get("is_sharp"):
        risk += min(0.25, _SOFT_CAP)
        reasons.append("SHARP")

    if not pred.get("live_data", True):
        risk += min(0.10, _SOFT_CAP)
        reasons.append("NO_LIVE_DATA")

    market_probs = [hw, aw, float(pred.get("over_1_5") or 0),
                    float(pred.get("over_2_5") or 0),
                    float(pred.get("btts") or 0)]
    best_market = max(market_probs) if market_probs else 0
    if best_market < 65.0:
        risk += min(0.15, _SOFT_CAP)
        reasons.append("LOW_PROB")

    # Draw "pollution": draw near top side makes it a 3-way coin flip
    if dr >= 30.0 and dr >= top_side - 10.0:
        risk += min(0.15, _SOFT_CAP)
        reasons.append("COIN_FLIP")

    # Form-fade: picked team on a losing streak contradicts the model
    fade_penalty, fade_reasons = _form_fade_penalty(pred)
    if fade_penalty > 0:
        risk += min(fade_penalty, _SOFT_CAP)
        for r in fade_reasons:
            if r not in reasons:
                reasons.append(r)

    return min(risk, 1.0), reasons


def score_risk_nba(pred: dict) -> tuple:
    """
    NBA-specific risk scoring.

    Returns (risk_score: float 0-1, reasons: list[str])

    Penalises:
      * True coin-flip (top_side < 52 %): HIGH_RISK
      * "Dirty zone" 52-58 %: moderate penalty
      * Borderline 58-62 %: small penalty
      * Missing live data from ESPN
      * BAJA/MEDIA confidence
      * Spread too narrow (if spread data available)
    """
    risk = 0.0
    reasons: list = []

    conf = pred.get("confidence", "BAJA")
    if conf == "BAJA":
        return 1.0, ["LOW_CONF"]
    if conf == "MEDIA":
        risk += 0.10
        reasons.append("LOW_CONF")

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    top_side = max(hw, aw)

    # Graduated probability penalty — no duplicate: only one block fires
    if top_side < 54.0:
        risk += 0.35
        reasons.append("COIN_FLIP")
    elif top_side < 58.0:
        risk += 0.25
        reasons.append("COIN_FLIP")
    elif top_side < 62.0:
        risk += 0.10
        reasons.append("LOW_PROB")

    if not pred.get("live_data", True):
        risk += 0.10   # lowered from 0.20
        reasons.append("DATA_MISSING")

    # Spread narrow (if available): "spread_line" close to 0
    spread_str = pred.get("spread_str", "")
    if spread_str:
        try:
            spread_val = abs(float(str(spread_str).replace("+", "").replace("−", "-")))
            if spread_val < 2.5:
                risk += 0.10
                reasons.append("SPREAD_NARROW")
        except (ValueError, TypeError):
            pass

    return min(risk, 1.0), reasons


def score_risk_nfl(pred: dict) -> tuple:
    """
    NFL-specific risk scoring (similar to NBA, adjusted for football variance).

    Returns (risk_score: float 0-1, reasons: list[str])
    """
    risk = 0.0
    reasons: list = []

    conf = pred.get("confidence", "BAJA")
    if conf == "BAJA":
        return 1.0, ["LOW_CONF"]
    if conf == "MEDIA":
        risk += 0.10
        reasons.append("LOW_CONF")

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    top_side = max(hw, aw)

    # Graduated probability penalty — single block, no duplicate
    if top_side < 54.0:
        risk += 0.35
        reasons.append("COIN_FLIP")
    elif top_side < 58.0:
        risk += 0.25
        reasons.append("COIN_FLIP")
    elif top_side < 62.0:
        risk += 0.10
        reasons.append("LOW_PROB")

    if not pred.get("live_data", True):
        risk += 0.10   # lowered from 0.20
        reasons.append("DATA_MISSING")

    return min(risk, 1.0), reasons


def score_risk_mlb(pred: dict) -> tuple:
    """
    MLB-specific risk scoring.

    Extra penalisation when ERA data is absent (DATA_MISSING).

    Returns (risk_score: float 0-1, reasons: list[str])
    """
    risk = 0.0
    reasons: list = []

    conf = pred.get("confidence", "BAJA")
    if conf == "BAJA":
        return 1.0, ["LOW_CONF"]
    if conf == "MEDIA":
        risk += 0.10
        reasons.append("LOW_CONF")

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    top_side = max(hw, aw)

    # Graduated probability penalty — single block, no duplicate
    if top_side < 54.0:
        risk += 0.35
        reasons.append("COIN_FLIP")
    elif top_side < 58.0:
        risk += 0.25
        reasons.append("COIN_FLIP")
    elif top_side < 62.0:
        risk += 0.10
        reasons.append("LOW_PROB")

    # Pitcher/ERA data strongly affects MLB accuracy.
    # pitcher_home/away are now populated by baseball.predict_game() as bools.
    has_era = pred.get("pitcher_home", False) or pred.get("home_era") is not None
    has_era_away = pred.get("pitcher_away", False) or pred.get("away_era") is not None
    if not has_era or not has_era_away:
        risk += 0.15   # lowered from 0.25; RPG data still provides a baseline
        reasons.append("DATA_MISSING")

    if not pred.get("live_data", True):
        risk += 0.10   # lowered from 0.15
        reasons.append("DATA_MISSING")

    return min(risk, 1.0), reasons


def score_risk_tennis(pred: dict) -> tuple:
    """
    Tennis-specific risk scoring (optional sport support).

    Returns (risk_score: float 0-1, reasons: list[str])
    """
    risk = 0.0
    reasons: list = []

    conf = pred.get("confidence", "BAJA")
    if conf == "BAJA":
        return 1.0, ["LOW_CONF"]
    if conf == "MEDIA":
        risk += 0.15
        reasons.append("LOW_CONF")

    p1 = float(pred.get("player1_win") or pred.get("home_win") or 0.0)
    p2 = float(pred.get("player2_win") or pred.get("away_win") or 0.0)
    top_side = max(p1, p2)

    if top_side < 60.0:
        risk += 0.30
        reasons.append("COIN_FLIP")
    elif top_side < 65.0:
        risk += 0.15
        reasons.append("LOW_SEPARATION")

    if not pred.get("live_data", True):
        risk += 0.10
        reasons.append("NO_LIVE_DATA")

    return min(risk, 1.0), reasons


def score_risk(pred: dict) -> tuple:
    """
    Dispatch to the appropriate per-sport risk scorer.

    Returns (risk_score: float 0-1, reasons: list[str])
    """
    sport = _sport_key(pred)
    if sport == "nba":
        return score_risk_nba(pred)
    if sport == "nfl":
        return score_risk_nfl(pred)
    if sport == "mlb":
        return score_risk_mlb(pred)
    if sport == "tennis":
        return score_risk_tennis(pred)
    return score_risk_soccer(pred)


# ── Calibration (pre-loaded, sample-gated) ───────────────────────────────────

# Sample-size buckets for calibration gating
_CAL_N_SKIP        = 30    # n < this → no calibration
_CAL_N_CONSERVATIVE = 100  # n < this → conservative EWMA (0.5 weight)

# Clamp bounds for safe-mode calibrated probabilities
_CAL_SAFE_MIN = 45.0   # lowered from 50.0
_CAL_SAFE_MAX = 92.0   # raised from 90.0


def calibrate_prob_gated(
    prob: float,
    market_type: str,
    cal_stats: dict,
    safe_mode: bool = False,
) -> tuple:
    """
    Calibration-adjusted probability with sample-size gating.

    Parameters
    ----------
    prob        : raw model probability (%)
    market_type : market type key ("moneyline", "totals", etc.)
    cal_stats   : pre-loaded dict from ``get_calibration_stats()``
    safe_mode   : if True, clamp output to [50, 90] %

    Returns
    -------
    (calibrated_prob: float, n_samples: int, bucket: str)
      ``bucket`` is "none" | "conservative" | "full"
    """
    from core.parlay_history import _get_factor  # reuse clamped-factor helper

    market_stats = cal_stats.get(market_type)
    overall_stats = cal_stats.get("overall")

    # Choose stats source: prefer per-market, fall back to overall
    stats = market_stats or overall_stats
    n     = stats["n"] if stats else 0

    if n < _CAL_N_SKIP:
        bucket = "none"
        adjusted = prob
    elif n < _CAL_N_CONSERVATIVE:
        bucket = "conservative"
        # Half-strength calibration: blend factor toward 1.0
        raw_factor = _get_factor(stats) or 1.0
        factor = 0.5 * (raw_factor + 1.0)
        adjusted = prob * factor
    else:
        bucket = "full"
        factor = _get_factor(stats) or 1.0
        adjusted = prob * factor

    # Calibration floor: prevent a downward-calibration "death spiral".
    # If calibration would reduce the probability by more than _CAL_FLOOR_MAX_REDUCTION pp,
    # cap the reduction so picks still get a fair chance at the filter.
    # 15 pp was chosen to:
    #   (a) preserve the meaningful difference between conservative (half-
    #       strength) and full calibration buckets (a full-strength factor
    #       of 0.70 reduces 80 % → 56 %, floored to 65 % = -15 pp; while
    #       conservative blends to 0.85, giving 68 % which is above the floor),
    #   (b) still prevent extreme overconfident factors from pushing good picks
    #       all the way below min_prob in a single bad stretch of results.
    if adjusted < prob - _CAL_FLOOR_MAX_REDUCTION:
        adjusted = prob - _CAL_FLOOR_MAX_REDUCTION

    adjusted = round(adjusted, 1)
    if safe_mode:
        adjusted = max(_CAL_SAFE_MIN, min(_CAL_SAFE_MAX, adjusted))

    return adjusted, n, bucket


# ── Candidate pick generation ─────────────────────────────────────────────────

def _build_candidates(pred: dict) -> list:
    """
    Generate all valid candidate picks for a single match prediction.

    Returns a list of dicts:
      {"pick": str, "prob": float, "market_type": str,
       "p_second": float}   ← second-best same-market probability for
                               clarity checks (0.0 if not applicable)

    Market types: "moneyline", "totals", "btts", "spread", "corners", "cards"
    """
    candidates = []
    sport_raw = str(pred.get("sport", "")).lower()
    home = pred.get("home", "Local")
    away = pred.get("away", "Visitante")

    # ── Moneyline (all sports) ─────────────────────────────────────────────
    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    dr = float(pred.get("draw") or 0.0)  # soccer draw

    # Build moneyline outcomes list for p_second calculation
    ml_outcomes = [o for o in [hw, aw, dr] if o > 0]
    ml_outcomes.sort(reverse=True)
    ml_second = ml_outcomes[1] if len(ml_outcomes) >= 2 else 0.0

    if hw > 0:
        candidates.append({
            "pick": f"Victoria {home}",
            "prob": hw,
            "market_type": "moneyline",
            "p_second": ml_second,
        })
    if aw > 0:
        candidates.append({
            "pick": f"Victoria {away}",
            "prob": aw,
            "market_type": "moneyline",
            "p_second": ml_second,
        })

    # ── Soccer-specific ────────────────────────────────────────────────────
    is_soccer = (not sport_raw) or any(
        k in sport_raw for k in ("soccer", "football", "⚽")
    )
    if is_soccer:
        o15  = float(pred.get("over_1_5") or 0.0)
        o25  = float(pred.get("over_2_5") or 0.0)
        o35  = float(pred.get("over_3_5") or 0.0)
        btts = float(pred.get("btts") or 0.0)

        if dr > 0:
            candidates.append({
                "pick": "Empate",
                "prob": dr,
                "market_type": "moneyline",
                "p_second": ml_second,
            })
        if o15 > 0:
            candidates.append({"pick": "Over 1.5", "prob": o15,
                                "market_type": "totals", "p_second": 0.0})
        if o25 > 0:
            candidates.append({"pick": "Over 2.5", "prob": o25,
                                "market_type": "totals", "p_second": 0.0})
        if o35 > 0:
            candidates.append({"pick": "Over 3.5", "prob": o35,
                                "market_type": "totals", "p_second": 0.0})
        if btts > 0:
            candidates.append({"pick": "Ambos Marcan (BTTS)", "prob": btts,
                                "market_type": "btts", "p_second": 0.0})

        # ── Corners ───────────────────────────────────────────────────────
        corners_mkt = pred.get("corners_market", {})
        if corners_mkt and isinstance(corners_mkt, dict):
            c_over  = float(corners_mkt.get("over_prob")  or 0.0)
            c_under = float(corners_mkt.get("under_prob") or 0.0)
            c_line  = float(corners_mkt.get("line")       or 9.5)
            if c_over > 0:
                candidates.append({
                    "pick": f"Más de {c_line:.1f} Córners",
                    "prob": c_over,
                    "market_type": "corners", "p_second": 0.0,
                })
            if c_under > 0:
                candidates.append({
                    "pick": f"Menos de {c_line:.1f} Córners",
                    "prob": c_under,
                    "market_type": "corners", "p_second": 0.0,
                })

        # ── Shots on target ───────────────────────────────────────────────
        sot = pred.get("shots_on_target", {})
        if sot and isinstance(sot, dict):
            sot_total = float(sot.get("sot_total") or 0.0)
            sot_line  = float(sot.get("line")      or 0.0)
            if sot_total > 0 and sot_line > 0:
                # Use Poisson to estimate P(SoT > sot_line)
                sot_over_prob  = _poisson_over(sot_total, sot_line)
                sot_under_prob = round(100.0 - sot_over_prob, 1)
                if sot_over_prob > 0:
                    candidates.append({
                        "pick": f"Más de {sot_line:.1f} Tiros a Puerta",
                        "prob": sot_over_prob,
                        "market_type": "shots", "p_second": 0.0,
                    })
                if sot_under_prob > 0:
                    candidates.append({
                        "pick": f"Menos de {sot_line:.1f} Tiros a Puerta",
                        "prob": sot_under_prob,
                        "market_type": "shots", "p_second": 0.0,
                    })

        # ── Cards ─────────────────────────────────────────────────────────
        cards_detail = pred.get("cards_detail", {})
        if cards_detail and isinstance(cards_detail, dict):
            total_cards = float(cards_detail.get("total_cards") or 0.0)
            if total_cards > 0:
                for cards_line in (3.5, 4.5):
                    cp = _poisson_over(total_cards, cards_line)
                    if cp > 0:
                        candidates.append({
                            "pick": f"Más de {cards_line} Tarjetas",
                            "prob": cp,
                            "market_type": "cards", "p_second": 0.0,
                        })

    # ── NBA game totals ──────────────────────────────────────────────────
    if "nba" in sport_raw:
        game_totals = pred.get("game_totals", {})
        projected   = float(pred.get("over_under") or 0.0)
        if game_totals and isinstance(game_totals, dict):
            line = float(game_totals.get("over_under_line") or
                         game_totals.get("line") or projected or 220.0)
            # Use over_prob/under_prob directly when provided (legacy format)
            over_p  = float(game_totals.get("over_prob")  or 0.0)
            under_p = float(game_totals.get("under_prob") or 0.0)
            if not over_p and projected > 0:
                over_p  = _ou_prob(projected, line)
                under_p = round(100.0 - over_p, 1)
            if over_p > 0:
                candidates.append({
                    "pick": f"Over {line:.1f} pts",
                    "prob": over_p,
                    "market_type": "totals", "p_second": 0.0,
                })
            if under_p > 0:
                candidates.append({
                    "pick": f"Under {line:.1f} pts",
                    "prob": under_p,
                    "market_type": "totals", "p_second": 0.0,
                })
        elif projected > 0:
            line    = float(round(projected))
            over_p  = _ou_prob(projected, line)
            under_p = round(100.0 - over_p, 1)
            if over_p > 0:
                candidates.append({
                    "pick": f"Over {line:.1f} pts",
                    "prob": over_p,
                    "market_type": "totals", "p_second": 0.0,
                })
            if under_p > 0:
                candidates.append({
                    "pick": f"Under {line:.1f} pts",
                    "prob": under_p,
                    "market_type": "totals", "p_second": 0.0,
                })

    # ── NFL game totals ──────────────────────────────────────────────────
    if "nfl" in sport_raw:
        projected = float(pred.get("over_under") or 0.0)
        if projected > 0:
            line    = float(round(projected * 2) / 2)   # nearest 0.5
            over_p  = _ou_prob(projected, line, sigma_frac=0.10)
            under_p = round(100.0 - over_p, 1)
            if over_p > 0:
                candidates.append({
                    "pick": f"Over {line:.1f} pts",
                    "prob": over_p,
                    "market_type": "totals", "p_second": 0.0,
                })
            if under_p > 0:
                candidates.append({
                    "pick": f"Under {line:.1f} pts",
                    "prob": under_p,
                    "market_type": "totals", "p_second": 0.0,
                })

    # ── MLB run line ──────────────────────────────────────────────────────
    if "mlb" in sport_raw:
        run_line = pred.get("run_line", {})
        if run_line:
            cov = float(run_line.get("cover_prob") or 0.0)
            fav = run_line.get("fav_side", "")
            if cov > 0:
                label = f"{home} -1.5" if fav == "home" else f"{away} -1.5"
                candidates.append({"pick": label, "prob": cov,
                                    "market_type": "spread", "p_second": 0.0})
            # O/U runs from run_line
            ou_runs = float(run_line.get("over_under") or 0.0)
            if ou_runs > 0:
                line    = float(round(ou_runs * 2) / 2)
                over_p  = _poisson_over(ou_runs, line)
                under_p = round(100.0 - over_p, 1)
                if over_p > 0:
                    candidates.append({
                        "pick": f"Over {line:.1f} Carreras",
                        "prob": over_p,
                        "market_type": "totals", "p_second": 0.0,
                    })
                if under_p > 0:
                    candidates.append({
                        "pick": f"Under {line:.1f} Carreras",
                        "prob": under_p,
                        "market_type": "totals", "p_second": 0.0,
                    })

    return candidates


# ── Clarity check ─────────────────────────────────────────────────────────────

def _passes_clarity(candidate: dict, pred: dict, safe_mode: bool) -> tuple:
    """
    Return ``(passes: bool, fail_reasons: list[str])``.

    In safe mode, moneyline picks must satisfy:
      * prob >= MIN_PROB_SAFE_ABS (absolute)
      * separation (prob - p_second) >= MIN_SEP_SAFE
      * draw only allowed if p_draw >= MIN_DRAW_PROB

    In default mode, no clarity check is applied (always passes).
    """
    if not safe_mode:
        return True, []

    mtype = candidate.get("market_type", "")
    if mtype != "moneyline":
        return True, []  # clarity only enforced on moneyline

    fail: list = []
    prob   = candidate["prob"]
    p_sec  = candidate.get("p_second", 0.0)
    sep    = prob - p_sec

    if prob < MIN_PROB_SAFE_ABS:
        fail.append("LOW_PROB")
    if sep < MIN_SEP_SAFE:
        fail.append("LOW_SEPARATION")

    # Extra check: is this the draw pick?
    if candidate["pick"] == "Empate":
        dr = float(pred.get("draw") or 0.0)
        if dr < MIN_DRAW_PROB:
            fail.append("LOW_PROB")  # draw excluded in safe mode unless very likely

    return len(fail) == 0, fail


# ── Main leg generation ───────────────────────────────────────────────────────

def generate_parlay_legs(
    predictions: list,
    max_legs: int = 5,
    min_confidence: str = MIN_CONF_DEFAULT,
    min_prob: float = MIN_PROB_DEFAULT,
    safe_mode: bool = False,
    cal_stats: Optional[dict] = None,
) -> tuple:
    """
    Generate individual parlay legs from a list of multi-sport predictions.

    Parameters
    ----------
    predictions    : list of prediction dicts (soccer, NBA, NFL, or MLB).
    max_legs       : maximum number of legs to return.
    min_confidence : minimum confidence level ("ALTA" or "MEDIA").
                     Defaults to ``MIN_CONF_DEFAULT`` ("ALTA").
    min_prob       : minimum individual pick probability (%) after calibration.
                     Defaults to ``MIN_PROB_DEFAULT`` (68.0).
    safe_mode      : if True, use conservative safe-mode filters (moneyline
                     only, clarity criterion, stricter risk threshold).
    cal_stats      : pre-loaded calibration stats dict.  If None the function
                     loads them once from ``parlay_history`` (lazy).

    Returns
    -------
    (legs: list, report: dict, excluded: list)

    ``legs``     — final selected leg dicts, sorted by prob descending.
    ``report``   — {"total_candidates": int, "legs_selected": int,
                    "exclusions": {"LOW_CONF": n, "LOW_PROB": n, ...}}
    ``excluded`` — list of excluded match dicts:
                   {event_name, sport, market_type, p_best, p_best_raw,
                    confidence, risk_score, reasons[]}
    """
    min_rank = _CONFIDENCE_RANK.get(min_confidence, 2)

    # ── Load calibration stats once ────────────────────────────────────────
    if cal_stats is None:
        try:
            from core.parlay_history import get_calibration_stats
            cal_stats = get_calibration_stats()
        except Exception:
            cal_stats = {}

    # ── Also load per-sport calibration stats (for overconfidence detection) ─
    sport_cal_stats: dict = {}
    try:
        from core.parlay_history import get_sport_stats
        sport_cal_stats = get_sport_stats()
    except Exception:
        pass

    risk_threshold = RISK_THRESHOLD_SAFE if safe_mode else RISK_THRESHOLD_DEFAULT
    allowed_markets = _SAFE_MODE_MARKETS if safe_mode else _DEFAULT_MODE_MARKETS

    # Effective safe-mode min_prob (override if caller passed the old default)
    eff_min_prob = min_prob
    if safe_mode and eff_min_prob >= 62.0:
        eff_min_prob = MIN_PROB_SAFE_ABS

    # Exclusion reason counters and excluded list
    excl_counts: dict = {}
    excluded: list = []
    pool: list = []

    def _add_excl(match_name, sport, mtype, p_best_raw, p_best_cal, conf,
                  risk_s, reasons):
        for r in reasons:
            excl_counts[r] = excl_counts.get(r, 0) + 1
        excluded.append({
            "event_name":  match_name,
            "sport":       sport,
            "market_type": mtype,
            "p_best":      p_best_cal,
            "p_best_raw":  p_best_raw,
            "confidence":  conf,
            "risk_score":  risk_s,
            "reasons":     reasons,
        })

    total_candidates = len(predictions)

    # ── Step 1: Filter, score risk, pick best candidate per match ──────────
    for pred in predictions:
        conf  = pred.get("confidence", "BAJA")
        sport = _sport_key(pred)
        match_str = f"{pred.get('home','Local')} vs {pred.get('away','Visitante')}"

        if _CONFIDENCE_RANK.get(conf, 0) < min_rank:
            _add_excl(match_str, sport, "", 0, 0, conf,
                      0.0, ["LOW_CONF"])
            continue

        risk_score, risk_reasons = score_risk(pred)

        # ── Overconfidence calibration boost ──────────────────────────────
        # If historical data shows this sport has been systematically
        # overconfident (calibration factor < threshold), add a small risk
        # penalty so overconfident markets face a higher bar.
        sport_stats = sport_cal_stats.get(sport, {})
        sport_cal_factor = sport_stats.get("calibration", 1.0) if sport_stats else 1.0
        if sport_cal_factor < _CAL_OVERCONF_THRESHOLD and sport_stats.get("n", 0) >= 5:
            risk_score = min(risk_score + _CAL_OVERCONF_PENALTY, 1.0)
            if "OVERCONF_HISTORY" not in risk_reasons:
                risk_reasons = list(risk_reasons) + ["OVERCONF_HISTORY"]

        if risk_score >= risk_threshold:
            _add_excl(match_str, sport, "", 0, 0, conf,
                      risk_score, risk_reasons or ["HIGH_RISK"])
            continue

        league = pred.get("league", "")
        emoji  = _sport_emoji(pred)

        candidates = _build_candidates(pred)

        # Filter to allowed market types in this mode
        candidates = [c for c in candidates if c["market_type"] in allowed_markets]
        if not candidates:
            _add_excl(match_str, sport, "N/A", 0, 0, conf,
                      risk_score, ["MARKET_NOT_WHITELISTED"])
            continue

        # Apply calibration to each candidate
        cal_candidates = []
        for c in candidates:
            raw_p = c["prob"]
            cal_p, n_samp, cal_bucket = calibrate_prob_gated(
                raw_p, c["market_type"], cal_stats, safe_mode=safe_mode
            )
            cal_candidates.append(dict(
                c,
                raw_prob=raw_p,
                prob=cal_p,
                n_samples_used=n_samp,
                cal_bucket=cal_bucket,
            ))

        # Clarity check (moneyline, safe mode only)
        clarity_ok = []
        for c in cal_candidates:
            passes, fail_r = _passes_clarity(c, pred, safe_mode)
            if passes:
                clarity_ok.append(c)
            else:
                _add_excl(match_str, sport, c["market_type"],
                          c["raw_prob"], c["prob"], conf,
                          risk_score, fail_r)
        cal_candidates = clarity_ok
        if not cal_candidates:
            continue

        # Probability threshold filter
        valid = [c for c in cal_candidates if c["prob"] >= eff_min_prob]
        if not valid:
            best_raw = max(c["raw_prob"] for c in cal_candidates)
            best_cal = max(c["prob"] for c in cal_candidates)
            best_mkt = max(cal_candidates, key=lambda x: x["prob"])["market_type"]
            _add_excl(match_str, sport, best_mkt,
                      best_raw, best_cal, conf, risk_score, ["LOW_PROB"])
            continue

        # Best pick = highest calibrated probability
        best = max(valid, key=lambda x: x["prob"])
        raw_p = best.get("raw_prob", best["prob"])
        cal_p = best["prob"]
        cal_note = (
            f"ajustado {raw_p:.1f}%→{cal_p:.1f}%"
            if abs(cal_p - raw_p) >= 0.5
            else ""
        )

        pool.append({
            "match":            match_str,
            "pick":             best["pick"],
            "prob":             round(cal_p, 1),
            "raw_prob":         round(raw_p, 1),
            "league":           league,
            "sport":            str(pred.get("sport", "soccer")).lower(),
            "confidence":       conf,
            "market_type":      best["market_type"],
            "sport_emoji":      emoji,
            "risk_score":       round(risk_score, 3),
            "risk_reasons":     risk_reasons,
            "n_samples_used":   best.get("n_samples_used", 0),
            "cal_bucket":       best.get("cal_bucket", "none"),
            "calibration_note": cal_note,
            "live_data":        bool(pred.get("live_data", True)),
            "sharp_flag":       bool((pred.get("sharp") or {}).get("is_sharp", False)),
            "p_second":         round(best.get("p_second", 0.0), 1),
        })

    # ── Step 2: Sort by calibrated probability descending ─────────────────
    pool.sort(key=lambda x: x["prob"], reverse=True)

    # ── Step 3: Apply variety / selection constraints ─────────────────────
    selected: list = []
    market_counts: dict = {}
    used_matches: set = set()

    for leg in pool:
        if len(selected) >= max_legs:
            break
        mtype      = leg["market_type"]
        match_name = leg["match"]
        if match_name in used_matches:
            # Duplicate match — should not happen but guard anyway
            continue
        if not safe_mode and market_counts.get(mtype, 0) >= _MAX_SAME_MARKET:
            # Variety cap only in default mode
            _add_excl(match_name, leg["sport"], mtype,
                      leg["raw_prob"], leg["prob"], leg["confidence"],
                      leg["risk_score"], ["VARIETY_CAP"])
            continue
        market_counts[mtype] = market_counts.get(mtype, 0) + 1
        used_matches.add(match_name)
        selected.append(leg)

    # ── Build report ──────────────────────────────────────────────────────
    report = {
        "total_candidates": total_candidates,
        "legs_selected":    len(selected),
        "mode":             "safe" if safe_mode else "default",
        "min_prob":         eff_min_prob,
        "risk_threshold":   risk_threshold,
        "exclusions":       dict(excl_counts),
    }

    return selected, report, excluded


# ── Parlay tiers ──────────────────────────────────────────────────────────────

def build_parlays(legs: list) -> dict:
    """
    Build 3 risk-tiered parlays from sorted legs.

    Tiers:
      SAFE     (🟢) — top 2 legs
      BALANCED (🟡) — top 3 legs
      RISKY    (🔴) — top 4-5 legs

    Combined probability = (p1/100) * (p2/100) * ... * 100
    """
    def _combined(selected_legs):
        prob = 1.0
        for leg in selected_legs:
            prob *= leg["prob"] / 100.0
        return round(prob * 100, 1)

    result = {"safe": None, "balanced": None, "risky": None}

    if len(legs) >= 2:
        safe_legs = legs[:2]
        result["safe"] = {
            "legs": safe_legs,
            "combined_prob": _combined(safe_legs),
        }

    if len(legs) >= 3:
        balanced_legs = legs[:3]
        result["balanced"] = {
            "legs": balanced_legs,
            "combined_prob": _combined(balanced_legs),
        }

    if len(legs) >= 4:
        risky_legs = legs[:min(5, len(legs))]
        result["risky"] = {
            "legs": risky_legs,
            "combined_prob": _combined(risky_legs),
        }

    return result


# ── Telegram formatters ───────────────────────────────────────────────────────

def format_parlay(
    parlays: dict,
    filtered_count: int = 0,
    parlay_id: str = "",
    report: Optional[dict] = None,
) -> str:
    """
    Format the parlay output for Telegram.

    When ``report`` is provided the exclusion summary is taken from there
    (replacing the legacy ``filtered_count``).
    """
    lines = [
        "╔══════════════════════════════════╗",
        "  🎰 PARLAY DEL DÍA — Sports Engine",
        "╚══════════════════════════════════╝",
        "",
    ]

    tiers = [
        ("safe",     "🟢", "SEGURA",     "2 patas"),
        ("balanced", "🟡", "BALANCEADA", "3 patas"),
        ("risky",    "🔴", "ARRIESGADA", "4+ patas"),
    ]

    _numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    any_calibrated = False

    for key, emoji, label, _ in tiers:
        tier = parlays.get(key)
        if not tier:
            continue

        tier_legs = tier["legs"]
        n_patas   = len(tier_legs)
        lines.append(f"{emoji} *{label}* ({n_patas} patas)")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for i, leg in enumerate(tier_legs):
            num     = _numbers[i] if i < len(_numbers) else f"{i + 1}."
            sport_e = leg.get("sport_emoji", "")
            prefix  = f"{sport_e} " if sport_e else ""
            cal     = leg.get("calibration_note", "")
            cal_tag = f" _↺{_md_escape(cal)}_" if cal else ""
            match_s = _md_escape(leg['match'])
            pick_s  = _md_escape(leg['pick'])
            lines.append(
                f"  {num} {prefix}{match_s} → {pick_s} ({leg['prob']}%){cal_tag}"
            )
            if cal:
                any_calibrated = True
        lines.append(f"📊 Prob. combinada: *{tier['combined_prob']}%*")
        lines.append("")

    if not any(parlays.get(k) for k in ("safe", "balanced", "risky")):
        lines.append("⚠️ No hay suficientes picks confiables para armar parlay hoy.")
        lines.append("")

    # Exclusion summary
    if report and report.get("exclusions"):
        excl = report["exclusions"]
        total_excl = sum(excl.values())
        parts = ", ".join(f"{k}={v}" for k, v in sorted(excl.items()))
        lines.append(
            f"🔍 _{total_excl} partido(s) excluido(s): {parts}_"
        )
        lines.append("")
    elif filtered_count > 0:
        lines.append(
            f"🔍 _{filtered_count} partido(s) excluido(s) por alto riesgo o baja confianza._"
        )
        lines.append("")

    if any_calibrated:
        lines.append("↺ _Prob. ajustada según historial de resultados._")
        lines.append("")

    if parlay_id:
        lines.append(f"🆔 ID: `{parlay_id}`")
        lines.append(
            f"📝 Reporta resultados: `/resultado {parlay_id} WLW`"
        )
        lines.append("_  (W=Ganó, L=Perdió, X=Cancelado — una letra por pata)_")
        lines.append("")

    lines.append("⚠️ _Las parlays son recreativas. Apuesta responsablemente._")
    return "\n".join(lines)


# ── Dream Parlay (Parlay Soñador) ─────────────────────────────────────────────

def _soccer_narrative(home: str, away: str, winner: str | None, btts: bool, high_scoring: bool) -> str:
    if winner == home:
        if high_scoring and btts:
            return f"🔥 {_md_escape(home)} arrasa en casa — goleada con ambos marcando"
        if high_scoring:
            return f"⚡ {_md_escape(home)} domina — noche goleadora en casa"
        return f"💪 {_md_escape(home)} se lleva los 3 puntos en casa"
    if winner == away:
        if high_scoring and btts:
            return f"🚀 {_md_escape(away)} de visita y marcando — partido abierto"
        return f"🎯 {_md_escape(away)} sorprende fuera de casa"
    return f"🤝 Se espera equilibrio — empate entre {_md_escape(home)} y {_md_escape(away)}"


def _nba_narrative(home: str, away: str, winner: str | None, total: float) -> str:
    if winner == home:
        if total >= 220:
            return f"💥 {_md_escape(home)} en casa y lluvia de puntos — noche explosiva"
        return f"🏀 {_md_escape(home)} controla el ritmo en casa"
    if winner == away:
        return f"🌟 {_md_escape(away)} de visita — upset posible"
    return f"🏀 {_md_escape(home)} vs {_md_escape(away)} — partido parejo"


def _generic_narrative(home: str, away: str, winner: str | None) -> str:
    if winner == home:
        return f"💪 {_md_escape(home)} favorito en casa"
    if winner == away:
        return f"🎯 {_md_escape(away)} viene a llevarse la victoria"
    return f"⚖️ {_md_escape(home)} vs {_md_escape(away)}"


def _build_dream_bundle(pred: dict) -> dict | None:
    """
    Build a dream bundle for a single match using ALL available markets.

    Picks are chosen for coherence (they tell one consistent story per match)
    and realistic probability.  Each sport uses its own set of markets:
    - Soccer  : moneyline + best totals line (Over 1.5/2.5/3.5) + BTTS
    - NBA     : moneyline + Over/Under total points (best side ≥ 50 %)
    - MLB     : moneyline + run line (when direction matches) + totals
    - NFL/others: moneyline + best available non-moneyline market

    Returns a bundle dict (one or more legs) or None if no valid candidate
    exists for the match.
    """
    home  = pred.get("home", "Local")
    away  = pred.get("away", "Visitante")
    sport = str(pred.get("sport", "soccer")).lower()

    sport_emoji_map = {
        "soccer": "⚽", "football": "⚽",
        "nba": "🏀", "nfl": "🏈", "mlb": "⚾", "tennis": "🎾",
    }
    sport_emoji = pred.get("sport_emoji", "")
    if not sport_emoji:
        for key, emoji in sport_emoji_map.items():
            if key in sport:
                sport_emoji = emoji
                break
        else:
            sport_emoji = "🎯"

    candidates = _build_candidates(pred)
    if not candidates:
        return None

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    dr = float(pred.get("draw")     or 0.0)

    # ── Determine the story direction ────────────────────────────────────────
    if hw >= aw and hw >= dr and hw >= 52.0:
        winner_direction = "home"
        winner_name      = home
        ml_pick = next((c for c in candidates if f"Victoria {home}" in c["pick"]), None)
    elif aw > hw and aw >= dr and aw >= 52.0:
        winner_direction = "away"
        winner_name      = away
        ml_pick = next((c for c in candidates if f"Victoria {away}" in c["pick"]), None)
    elif dr >= 40.0:
        winner_direction = "draw"
        winner_name      = None
        ml_pick = next((c for c in candidates if c["pick"] == "Empate"), None)
    else:
        # No clear moneyline direction — use the best available candidate
        winner_direction = None
        winner_name      = None
        ml_pick          = None

    # ── Build coherent pick selection per sport ───────────────────────────────
    is_soccer = (not sport) or any(k in sport for k in ("soccer", "football"))

    selected: list[dict] = []

    # Always anchor on the moneyline when there is a clear direction
    if ml_pick:
        selected.append({**ml_pick})

    if is_soccer:
        o15  = float(pred.get("over_1_5") or 0.0)
        o25  = float(pred.get("over_2_5") or 0.0)
        o35  = float(pred.get("over_3_5") or 0.0)
        btts = float(pred.get("btts")     or 0.0)

        if winner_direction in ("home", "away"):
            # Active game expected — prefer the highest Over line with ≥ 50 %
            if o35 >= 50.0:
                cand = next((c for c in candidates if c["pick"] == "Over 3.5"), None)
                if cand:
                    selected.append(cand)
            elif o25 >= 50.0:
                cand = next((c for c in candidates if c["pick"] == "Over 2.5"), None)
                if cand:
                    selected.append(cand)
            elif o15 >= 55.0:
                cand = next((c for c in candidates if c["pick"] == "Over 1.5"), None)
                if cand:
                    selected.append(cand)

            # BTTS coherent only when it's not a likely blowout (< 75 % win prob)
            win_prob = max(hw, aw)
            if btts >= 50.0 and win_prob < 75.0:
                cand = next((c for c in candidates if "BTTS" in c["pick"]), None)
                if cand and not any("BTTS" in s["pick"] for s in selected):
                    selected.append(cand)

        elif winner_direction == "draw":
            # Draw story: moderate scoring expected — Over 1.5/2.5 OK, Over 3.5 NOT
            if o25 >= 50.0:
                cand = next((c for c in candidates if c["pick"] == "Over 2.5"), None)
                if cand:
                    selected.append(cand)
            elif o15 >= 55.0:
                cand = next((c for c in candidates if c["pick"] == "Over 1.5"), None)
                if cand:
                    selected.append(cand)
            # BTTS coherent with draws (both teams usually score)
            if btts >= 50.0:
                cand = next((c for c in candidates if "BTTS" in c["pick"]), None)
                if cand and not any("BTTS" in s["pick"] for s in selected):
                    selected.append(cand)

        else:
            # No clear direction — use the best available totals line
            for line_name, line_prob in [
                ("Over 1.5", o15), ("Over 2.5", o25), ("Over 3.5", o35)
            ]:
                if line_prob >= 55.0:
                    cand = next((c for c in candidates if c["pick"] == line_name), None)
                    if cand:
                        selected.append(cand)
                        break

        # ── Corners: best side (Over/Under) if prob ≥ 55 % ────────────────
        corners_cands = [c for c in candidates
                         if c["market_type"] == "corners" and c["prob"] >= 55.0]
        if corners_cands:
            selected.append(max(corners_cands, key=lambda c: c["prob"]))

        # ── Shots on target: best side if prob ≥ 55 % ────────────────────
        shots_cands = [c for c in candidates
                       if c["market_type"] == "shots" and c["prob"] >= 55.0]
        if shots_cands:
            selected.append(max(shots_cands, key=lambda c: c["prob"]))

        # ── Cards: Over 3.5 or Over 4.5 if prob ≥ 55 % ───────────────────
        cards_cands = sorted(
            [c for c in candidates
             if c["market_type"] == "cards" and c["prob"] >= 55.0],
            key=lambda c: c["prob"], reverse=True,
        )
        if cards_cands:
            selected.append(cards_cands[0])

        high_scoring = o25 >= 50.0
        btts_story   = btts >= 50.0 and winner_direction != "draw"
        narrative = _soccer_narrative(home, away, winner_name, btts_story, high_scoring)

    elif "nba" in sport:
        total_pts = float(pred.get("over_under") or 0.0)
        # Use totals candidates generated by _build_candidates (Over/Under pts)
        totals_cands = [c for c in candidates
                        if c["market_type"] == "totals" and c["prob"] >= 50.0]
        if totals_cands:
            selected.append(max(totals_cands, key=lambda c: c["prob"]))
        narrative = _nba_narrative(home, away, winner_name, total_pts)

    elif "mlb" in sport:
        run_line = pred.get("run_line", {})
        if run_line and isinstance(run_line, dict):
            cov = float(run_line.get("cover_prob") or 0.0)
            fav = run_line.get("fav_side", "")
            # Run line coherent only when it aligns with the moneyline direction
            rl_aligns = (
                (fav == "home" and winner_direction == "home") or
                (fav == "away" and winner_direction == "away")
            )
            if cov >= 52.0 and fav and rl_aligns:
                rl_label = f"{home} -1.5" if fav == "home" else f"{away} -1.5"
                selected.append({"pick": rl_label, "prob": round(cov, 1),
                                  "market_type": "spread"})
        # Add runs O/U from candidates (e.g., Over 9.0 Carreras)
        totals_cands = [c for c in candidates
                        if c["market_type"] == "totals" and c["prob"] >= 50.0]
        if totals_cands:
            selected.append(max(totals_cands, key=lambda c: c["prob"]))
        narrative = _generic_narrative(home, away, winner_name)

    elif "nfl" in sport:
        total_pts = float(pred.get("over_under") or 0.0)
        # Use totals candidates generated by _build_candidates (Over/Under pts)
        totals_cands = [c for c in candidates
                        if c["market_type"] == "totals" and c["prob"] >= 50.0]
        if totals_cands:
            selected.append(max(totals_cands, key=lambda c: c["prob"]))
        narrative = _generic_narrative(home, away, winner_name)

    else:
        # Other sports: add the best non-moneyline pick available
        non_ml = [c for c in candidates
                  if c["market_type"] != "moneyline" and c["prob"] >= 50.0]
        if non_ml:
            selected.append(max(non_ml, key=lambda c: c["prob"]))
        narrative = _generic_narrative(home, away, winner_name)

    # ── Fallback: if still nothing selected, take the two highest-prob picks ──
    if not selected:
        sorted_cands = sorted(candidates, key=lambda c: c["prob"], reverse=True)
        for cand in sorted_cands:
            if not selected:
                selected.append(cand)
            elif cand["market_type"] != "moneyline" or not any(
                s["market_type"] == "moneyline" for s in selected
            ):
                selected.append(cand)
            if len(selected) >= 2:
                break
        if not selected:
            return None
        narrative = _generic_narrative(home, away, winner_name)

    # ── Remove duplicate picks ────────────────────────────────────────────────
    seen_picks: set[str] = set()
    unique_selected: list[dict] = []
    for s in selected:
        if s["pick"] not in seen_picks:
            seen_picks.add(s["pick"])
            unique_selected.append(s)
    selected = unique_selected

    if not selected:
        return None

    bundle_prob = 1.0
    for leg in selected:
        bundle_prob *= leg["prob"] / 100.0

    return {
        "match":        f"{home} vs {away}",
        "sport":        sport,
        "sport_emoji":  sport_emoji,
        "narrative":    narrative,
        "legs":         selected,
        "bundle_prob":  round(bundle_prob * 100, 1),
    }


def generate_dream_parlay(predictions: list, max_bundles: int = 9999) -> list:
    """
    Generate a high-risk, high-reward dream parlay covering ALL available matches.

    Every prediction in ``predictions`` is processed (no artificial 4-bundle
    cap).  Sports are interleaved in round-robin order so the output covers all
    available sports, not just the highest-certainty ones.

    ``max_bundles`` can be used to cap the result if needed (default: unlimited).

    Bundle structure::

        {
            "match":       "Liverpool vs Man Utd",
            "sport":       "soccer",
            "sport_emoji": "⚽",
            "narrative":   "🔥 Liverpool arrasa en casa...",
            "legs":        [{"pick": str, "prob": float, "market_type": str}, ...],
            "bundle_prob": 27.1,
        }
    """
    def _certainty(pred: dict) -> float:
        hw = float(pred.get("home_win") or 0.0)
        aw = float(pred.get("away_win") or 0.0)
        return max(hw, aw)

    # Group by sport and sort each group by certainty (best match first)
    sport_groups: dict[str, list] = {}
    for pred in predictions:
        sport_key = str(pred.get("sport", "unknown")).lower()
        sport_groups.setdefault(sport_key, []).append(pred)
    for key in sport_groups:
        sport_groups[key].sort(key=_certainty, reverse=True)

    # Round-robin across sports so every sport is represented
    ordered: list[dict] = []
    iters = {s: iter(preds) for s, preds in sport_groups.items()}
    while iters:
        finished = []
        for sport_key in list(iters.keys()):
            try:
                ordered.append(next(iters[sport_key]))
            except StopIteration:
                finished.append(sport_key)
        for s in finished:
            del iters[s]

    bundles: list[dict] = []
    for pred in ordered:
        if len(bundles) >= max_bundles:
            break
        bundle = _build_dream_bundle(pred)
        if bundle is None:
            continue
        bundles.append(bundle)

    return bundles


def format_parlay_dream(bundles: list, parlay_id: str = "") -> str:
    """
    Format the /parlay_dream output for Telegram.

    Covers ALL available sports and markets.  Each bundle shows the coherent
    picks for one match.  A sport-breakdown summary is appended at the end.

    All user-supplied text (team names) is escaped so Telegram Markdown never
    trips on underscores or other special characters.
    """
    lines = [
        "╔══════════════════════════════════╗",
        "  🌙 PARLAY SOÑADOR — Sports Engine",
        "╚══════════════════════════════════╝",
        "",
        "💭 _Todos los mercados disponibles — coherencia máxima._",
        "_Cada partido, una historia. ¿Cuántas se harán realidad?_",
        "",
    ]

    _numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]

    if not bundles:
        lines.append("⚠️ _No hay suficientes partidos con mercados coherentes hoy._")
        lines.append("_Intenta mañana o usa `/parlay` para opciones estándar._")
        lines.append("")
    else:
        total_legs    = 0
        combined_prob = 1.0

        # Track sport counts for the breakdown summary
        sport_counts: dict[str, int] = {}

        for bundle in bundles:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            sport_emoji = bundle.get("sport_emoji", "🎯")
            match_s     = _md_escape(bundle["match"])
            lines.append(f"{sport_emoji} *{match_s}*")
            lines.append(bundle["narrative"])
            lines.append("")
            for j, leg in enumerate(bundle["legs"]):
                num    = _numbers[j] if j < len(_numbers) else f"{j+1}."
                pick_s = _md_escape(leg["pick"])
                lines.append(f"  {num} {pick_s} ({leg['prob']}%)")
            lines.append(f"  📊 Bundle: *{bundle['bundle_prob']}%*")
            lines.append("")
            total_legs    += len(bundle["legs"])
            combined_prob *= bundle["bundle_prob"] / 100.0

            # Accumulate sport breakdown
            s_emoji = bundle.get("sport_emoji", "🎯")
            sport_counts[s_emoji] = sport_counts.get(s_emoji, 0) + 1

        combined_pct = round(combined_prob * 100, 1)
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📊 *Prob. combinada total: {combined_pct}%*")
        lines.append(f"🎰 *Patas totales: {total_legs}*")
        lines.append(f"🗂 *Partidos: {len(bundles)}*")
        lines.append("🌙 *Riesgo: SOÑADOR*")
        lines.append("")

        # Sport breakdown
        if sport_counts:
            breakdown = "  ".join(
                f"{emoji} ×{n}" for emoji, n in sorted(sport_counts.items())
            )
            lines.append(f"🏟 _Deportes incluidos: {breakdown}_")
            lines.append("")

    if parlay_id:
        total_legs_count = sum(len(b["legs"]) for b in bundles) if bundles else 1
        result_template  = "W" * total_legs_count
        lines.append(f"🆔 ID: `{parlay_id}`")
        lines.append(f"📝 Reporta resultados: `/resultado {parlay_id} {result_template}`")
        lines.append("_  (W=Ganó, L=Perdió, X=Cancelado — una letra por pata)_")
        lines.append("")

    lines.append("⚠️ _Parlay recreativo de alto riesgo. Apuesta solo lo que estés dispuesto a perder._")
    return "\n".join(lines)


def format_parlay_safe(
    legs: list,
    report: dict,
    parlay_id: str = "",
) -> str:
    """
    Format the /parlay_safe output for Telegram.

    Shows the selected legs with calibrated probability and risk indicator,
    plus a compact exclusion-reason summary.
    """
    lines = [
        "╔══════════════════════════════════╗",
        "  🎯 PARLAY SAFE — Máximo Hit Rate",
        "╚══════════════════════════════════╝",
        "",
    ]

    _numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    _risk_icon = {True: "⚠️", False: ""}

    if legs:
        lines.append(f"🟢 *SEGURA* ({len(legs)} patas)")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for i, leg in enumerate(legs):
            num     = _numbers[i] if i < len(_numbers) else f"{i + 1}."
            sport_e = leg.get("sport_emoji", "")
            prefix  = f"{sport_e} " if sport_e else ""
            cal     = leg.get("calibration_note", "")
            cal_tag = f" _↺{_md_escape(cal)}_" if cal else ""
            risk_s  = leg.get("risk_score", 0.0)
            risk_lbl = f" `r={risk_s:.2f}`" if risk_s > 0 else ""
            sep     = leg.get("p_second", 0.0)
            sep_lbl = f" sep={leg['prob']-sep:.0f}pp" if sep > 0 else ""
            match_s = _md_escape(leg['match'])
            pick_s  = _md_escape(leg['pick'])
            lines.append(
                f"  {num} {prefix}{match_s} → *{pick_s}*"
                f" ({leg['prob']}%{risk_lbl}{sep_lbl}){cal_tag}"
            )
        # Combined probability
        if legs:
            combined = 1.0
            for leg in legs:
                combined *= leg["prob"] / 100.0
            lines.append(f"📊 Prob. combinada: *{round(combined*100, 1)}%*")
        lines.append("")
    else:
        lines.append("⚠️ _No hay patas suficientes con criterios safe hoy._")
        lines.append("")

    # Exclusion summary
    total = report.get("total_candidates", 0)
    sel   = report.get("legs_selected", len(legs))
    excl  = report.get("exclusions", {})
    lines.append("🔍 *Resumen de filtros*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"  Candidatos totales: `{total}`")
    lines.append(f"  Legs elegidas: `{sel}`")
    if excl:
        for reason, cnt in sorted(excl.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason}: `{cnt}`")
    lines.append("")

    if parlay_id:
        lines.append(f"🆔 ID: `{parlay_id}`")
        lines.append(
            f"📝 Reporta resultados: `/resultado {parlay_id} {'W'*max(len(legs),1)}`"
        )
        lines.append("_  (W=Ganó, L=Perdió, X=Cancelado — una letra por pata)_")
        lines.append("")

    lines.append("⚠️ _Las parlays son recreativas. Apuesta responsablemente._")
    return "\n".join(lines)
