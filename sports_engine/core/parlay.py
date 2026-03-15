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

_CONFIDENCE_RANK = {"ALTA": 2, "MEDIA": 1, "BAJA": 0}

# Maximum legs of the same market type allowed (default mode)
_MAX_SAME_MARKET = 2

# ── Risk thresholds (0-1 scale) ────────────────────────────────────────────────
RISK_THRESHOLD_DEFAULT = 0.35   # default /parlay
RISK_THRESHOLD_SAFE    = 0.25   # /parlay_safe (more conservative)

# Keep legacy name for test compatibility
_HIGH_RISK_SCORE = RISK_THRESHOLD_DEFAULT

# ── Safe-mode clarity thresholds ─────────────────────────────────────────────
# Minimum absolute probability of the best outcome (safe mode)
MIN_PROB_SAFE_ABS = 62.0
# Minimum gap between best and second-best outcome (percentage points)
MIN_SEP_SAFE      = 12.0
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
    """
    risk = 0.0
    reasons: list = []

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    dr = float(pred.get("draw") or 0.0)
    top_side = max(hw, aw)

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
        risk += 0.15
        reasons.append("LOW_CONF")

    sharp = pred.get("sharp", {})
    if sharp and sharp.get("is_sharp"):
        risk += 0.25
        reasons.append("SHARP")

    if not pred.get("live_data", True):
        risk += 0.10
        reasons.append("NO_LIVE_DATA")

    market_probs = [hw, aw, float(pred.get("over_1_5") or 0),
                    float(pred.get("over_2_5") or 0),
                    float(pred.get("btts") or 0)]
    best_market = max(market_probs) if market_probs else 0
    if best_market < 65.0:
        risk += 0.15
        reasons.append("LOW_PROB")

    # Draw "pollution": draw near top side makes it a 3-way coin flip
    if dr >= 30.0 and dr >= top_side - 10.0:
        risk += 0.15
        reasons.append("COIN_FLIP")

    return min(risk, 1.0), reasons


def score_risk_nba(pred: dict) -> tuple:
    """
    NBA-specific risk scoring.

    Returns (risk_score: float 0-1, reasons: list[str])

    Penalises:
      * "Dirty zone" home_win 52-58 % (too close to call)
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
        risk += 0.15
        reasons.append("LOW_CONF")

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    top_side = max(hw, aw)

    # "Dirty zone": margin too small to trust
    if 52.0 <= top_side <= 58.0:
        risk += 0.30
        reasons.append("COIN_FLIP")
    elif top_side < 60.0:
        risk += 0.15
        reasons.append("LOW_SEPARATION")

    if not pred.get("live_data", True):
        risk += 0.20
        reasons.append("DATA_MISSING")

    # Spread narrow (if available): "spread_line" close to 0
    spread_str = pred.get("spread_str", "")
    if spread_str:
        try:
            spread_val = abs(float(str(spread_str).replace("+", "").replace("−", "-")))
            if spread_val < 2.5:
                risk += 0.15
                reasons.append("SPREAD_NARROW")
        except (ValueError, TypeError):
            pass

    if top_side < 62.0:
        risk += 0.15
        reasons.append("LOW_PROB")

    return min(risk, 1.0), reasons


def score_risk_nfl(pred: dict) -> tuple:
    """
    NFL-specific risk scoring (similar to NBA, slightly stricter on data).

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

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    top_side = max(hw, aw)

    if top_side < 55.0:
        risk += 0.35
        reasons.append("COIN_FLIP")
    elif top_side < 62.0:
        risk += 0.20
        reasons.append("LOW_SEPARATION")

    if not pred.get("live_data", True):
        risk += 0.20
        reasons.append("DATA_MISSING")

    if top_side < 62.0:
        risk += 0.10
        reasons.append("LOW_PROB")

    return min(risk, 1.0), reasons


def score_risk_mlb(pred: dict) -> tuple:
    """
    MLB-specific risk scoring.

    Extra penalisation when pitcher / ERA data is absent (DATA_MISSING).

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

    hw = float(pred.get("home_win") or 0.0)
    aw = float(pred.get("away_win") or 0.0)
    top_side = max(hw, aw)

    if top_side < 55.0:
        risk += 0.35
        reasons.append("COIN_FLIP")
    elif top_side < 62.0:
        risk += 0.20
        reasons.append("LOW_SEPARATION")

    # Pitcher/ERA data strongly affects MLB accuracy
    if not pred.get("pitcher_home") or not pred.get("pitcher_away"):
        risk += 0.25
        reasons.append("DATA_MISSING")

    if not pred.get("live_data", True):
        risk += 0.15
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
_CAL_SAFE_MIN = 50.0
_CAL_SAFE_MAX = 90.0


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

    Market types: "moneyline", "totals", "btts", "spread"
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
    min_confidence: str = "ALTA",
    min_prob: float = 75.0,
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
    min_prob       : minimum individual pick probability (%) after calibration.
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

    risk_threshold = RISK_THRESHOLD_SAFE if safe_mode else RISK_THRESHOLD_DEFAULT
    allowed_markets = _SAFE_MODE_MARKETS if safe_mode else _DEFAULT_MODE_MARKETS

    # Effective safe-mode min_prob (override if caller passed the default)
    eff_min_prob = min_prob
    if safe_mode and eff_min_prob >= 75.0:
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
            cal_tag = f" _↺{cal}_" if cal else ""
            lines.append(
                f"  {num} {prefix}{leg['match']} → {leg['pick']} ({leg['prob']}%){cal_tag}"
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
            f"📝 _Reporta resultados: `/resultado {parlay_id} WLW`_"
        )
        lines.append("_  (W=Ganó, L=Perdió, X=Cancelado — una letra por pata)_")
        lines.append("")

    lines.append("⚠️ _Las parlays son recreativas. Apuesta responsablemente._")
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
            cal_tag = f" _↺{cal}_" if cal else ""
            risk_s  = leg.get("risk_score", 0.0)
            risk_lbl = f" `r={risk_s:.2f}`" if risk_s > 0 else ""
            sep     = leg.get("p_second", 0.0)
            sep_lbl = f" sep={leg['prob']-sep:.0f}pp" if sep > 0 else ""
            lines.append(
                f"  {num} {prefix}{leg['match']} → *{leg['pick']}*"
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
        lines.append(f"�� ID: `{parlay_id}`")
        lines.append(
            f"📝 _Reporta resultados: `/resultado {parlay_id} {'W'*max(len(legs),1)}`_"
        )
        lines.append("_  (W=Ganó, L=Perdió, X=Cancelado — una letra por pata)_")
        lines.append("")

    lines.append("⚠️ _Las parlays son recreativas. Apuesta responsablemente._")
    return "\n".join(lines)
