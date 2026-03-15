"""
Parlay (Combinada) Generator — multi-sport edition.

Builds reliable multi-leg parlays from the day's matches (Soccer, NBA, NFL, MLB)
by:
1. Running predictions for all today's matches across sports.
2. Filtering to only ALTA confidence picks (configurable) above a minimum
   probability threshold (default 75 %).
3. Scoring each match for risk and excluding high-risk games.
4. Selecting the best legs while enforcing market-type variety
   (no more than _MAX_SAME_MARKET legs of the same type per parlay).
5. Only one leg per match/event.
6. Computing combined probability for three risk tiers:
     SAFE (2 legs) · BALANCED (3 legs) · RISKY (4-5 legs).

Each leg dict:
  match, pick, prob, league, confidence, market_type, sport_emoji, risk_reasons
"""

import math

_CONFIDENCE_RANK = {"ALTA": 2, "MEDIA": 1, "BAJA": 0}

# Maximum legs of the same market type allowed in a single parlay tier
_MAX_SAME_MARKET = 2

# Minimum win-probability spread between top side and 50 % to be considered
# "non-balanced" enough for a moneyline parlay leg (top side must be >= this)
_MIN_ML_PROB = 55.0

# Risk thresholds
_HIGH_RISK_SCORE = 0.5   # matches scoring >= this are excluded from parlays

# Sport emoji lookup (matches the "sport" field pattern in prediction dicts)
_SPORT_EMOJI = {
    "nba": "🏀",
    "nfl": "🏈",
    "mlb": "⚾",
    "soccer": "⚽",
    "football": "⚽",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# ── Risk scoring ───────────────────────────────────────────────────────────────

def score_risk(pred: dict) -> tuple:
    """
    Score the risk of a match prediction.

    Returns
    -------
    (risk_score: float, reasons: list[str])
      risk_score is in [0.0, 1.0].  A match is flagged as *high risk* when
      risk_score >= _HIGH_RISK_SCORE (0.5).

    Heuristics (cumulative):
      - Win probabilities too balanced (top side < 55 %): +0.40
      - Win probabilities somewhat balanced (top side < 60 %): +0.20
      - Low confidence (BAJA): +0.40 | medium confidence (MEDIA): +0.15
      - Sharp-game detected for soccer: +0.25
      - No live stats available: +0.10
      - Highest market probability < 65 %: +0.20
    """
    risk = 0.0
    reasons: list = []

    hw = pred.get("home_win", 0.0)
    aw = pred.get("away_win", 0.0)
    top_side = max(hw, aw)

    if top_side < _MIN_ML_PROB:
        risk += 0.40
        reasons.append(f"Probs muy equilibradas ({top_side:.1f}%)")
    elif top_side < 60.0:
        risk += 0.20
        reasons.append(f"Probs balanceadas ({top_side:.1f}%)")

    conf = pred.get("confidence", "BAJA")
    if conf == "BAJA":
        risk += _HIGH_RISK_SCORE + 0.05  # BAJA confidence always exceeds the high-risk threshold
        reasons.append("Confianza BAJA")
    elif conf == "MEDIA":
        risk += 0.15
        reasons.append("Confianza MEDIA")

    # Sharp-game detection (soccer only)
    sharp = pred.get("sharp", {})
    if sharp and sharp.get("is_sharp"):
        risk += 0.25
        reasons.append("Sharp game detectado")

    # Missing live stats → higher model uncertainty
    if not pred.get("live_data", True):
        risk += 0.10
        reasons.append("Sin datos en vivo")

    # No market with a strong probability
    market_probs = [
        pred.get("home_win", 0),
        pred.get("away_win", 0),
        pred.get("over_1_5", 0),
        pred.get("over_2_5", 0),
        pred.get("btts", 0),
    ]
    best_market = max(market_probs) if market_probs else 0
    if best_market < 65.0:
        risk += 0.20
        reasons.append("Ningún mercado con prob > 65%")

    return min(risk, 1.0), reasons


# ── Candidate pick generation ──────────────────────────────────────────────────

def _build_candidates(pred: dict) -> list:
    """
    Generate all valid candidate picks for a single match prediction.

    Returns a list of dicts:
      {"pick": str, "prob": float, "market_type": str}

    Market types: "moneyline", "totals", "btts", "spread"

    Sport coverage
    --------------
    Soccer : moneyline (home/draw/away), totals (Over 1.5/2.5/3.5), BTTS
    NBA    : moneyline (home/away win)
    NFL    : moneyline (home/away win)
    MLB    : moneyline (home/away win), spread (run line cover probability)
    """
    candidates = []
    sport_raw = str(pred.get("sport", "")).lower()
    home = pred.get("home", "Local")
    away = pred.get("away", "Visitante")

    # ── Moneyline (all sports) ──────────────────────────────────────────────
    hw = pred.get("home_win", 0.0)
    aw = pred.get("away_win", 0.0)
    if hw > 0:
        candidates.append({"pick": f"Victoria {home}", "prob": hw, "market_type": "moneyline"})
    if aw > 0:
        candidates.append({"pick": f"Victoria {away}", "prob": aw, "market_type": "moneyline"})

    # ── Soccer-specific markets ────────────────────────────────────────────
    # Positive check: treat a match as soccer when the sport field contains a
    # known soccer keyword, or when the sport field is blank (default fallback).
    is_soccer = (not sport_raw) or any(
        k in sport_raw for k in ("soccer", "football", "⚽")
    )
    if is_soccer:
        dr = pred.get("draw", 0.0)
        o15 = pred.get("over_1_5", 0.0)
        o25 = pred.get("over_2_5", 0.0)
        o35 = pred.get("over_3_5", 0.0)
        btts = pred.get("btts", 0.0)

        if dr > 0:
            candidates.append({"pick": "Empate", "prob": dr, "market_type": "moneyline"})
        if o15 > 0:
            candidates.append({"pick": "Over 1.5", "prob": o15, "market_type": "totals"})
        if o25 > 0:
            candidates.append({"pick": "Over 2.5", "prob": o25, "market_type": "totals"})
        if o35 > 0:
            candidates.append({"pick": "Over 3.5", "prob": o35, "market_type": "totals"})
        if btts > 0:
            candidates.append({"pick": "Ambos Marcan (BTTS)", "prob": btts, "market_type": "btts"})

    # ── MLB run line ───────────────────────────────────────────────────────
    if "mlb" in sport_raw:
        run_line = pred.get("run_line", {})
        if run_line:
            cov = run_line.get("cover_prob", 0.0)
            fav = run_line.get("fav_side", "")
            if cov > 0:
                label = f"{home} -1.5" if fav == "home" else f"{away} -1.5"
                candidates.append({"pick": label, "prob": cov, "market_type": "spread"})

    return candidates


# ── Main leg generation ────────────────────────────────────────────────────────

def generate_parlay_legs(
    predictions: list,
    max_legs: int = 5,
    min_confidence: str = "ALTA",
    min_prob: float = 75.0,
) -> list:
    """
    Generate individual parlay legs from a list of multi-sport predictions.

    Parameters
    ----------
    predictions    : list of prediction dicts (soccer, NBA, NFL, or MLB).
    max_legs       : maximum number of legs to return.
    min_confidence : minimum confidence level to include ("ALTA" or "MEDIA").
    min_prob       : minimum individual pick probability (%) to include.
                     The threshold is applied *after* calibration so that
                     historically over-confident markets are automatically
                     filtered more strictly.

    Returns
    -------
    list of dicts, each:
      {
        "match"            : str,
        "pick"             : str,
        "prob"             : float,   ← calibration-adjusted probability
        "raw_prob"         : float,   ← original model probability
        "league"           : str,
        "confidence"       : str,
        "market_type"      : str,
        "sport_emoji"      : str,
        "risk_reasons"     : list[str],
        "calibration_note" : str,     ← "" if no adjustment was made
      }
    Sorted by prob descending, limited to max_legs, with market-variety
    constraints applied.
    """
    min_rank = _CONFIDENCE_RANK.get(min_confidence, 2)

    # Load calibration function once (graceful no-op if history unavailable)
    try:
        from core.parlay_history import calibrate_prob as _calibrate
    except Exception:
        _calibrate = None

    # ── Step 1: Filter, score risk, pick best candidate per match ─────────
    pool = []

    for pred in predictions:
        conf = pred.get("confidence", "BAJA")
        if _CONFIDENCE_RANK.get(conf, 0) < min_rank:
            continue

        risk_score, risk_reasons = score_risk(pred)
        if risk_score >= _HIGH_RISK_SCORE:
            continue

        home = pred.get("home", "Local")
        away = pred.get("away", "Visitante")
        league = pred.get("league", "")
        emoji = _sport_emoji(pred)
        match_str = f"{home} vs {away}"

        candidates = _build_candidates(pred)

        # Apply calibration to each candidate before threshold filtering
        if _calibrate is not None:
            adjusted = []
            for c in candidates:
                raw_p  = c["prob"]
                cal_p  = _calibrate(raw_p, c["market_type"])
                adjusted.append(dict(c, raw_prob=raw_p, prob=cal_p))
            candidates = adjusted
        else:
            for c in candidates:
                c.setdefault("raw_prob", c["prob"])

        valid = [c for c in candidates if c["prob"] >= min_prob]
        if not valid:
            continue

        # Best pick per match = highest calibrated probability among valid candidates
        best = max(valid, key=lambda x: x["prob"])
        raw_p = best.get("raw_prob", best["prob"])
        cal_note = (
            f"ajustado {raw_p:.1f}%→{best['prob']:.1f}%"
            if abs(best["prob"] - raw_p) >= 0.5
            else ""
        )

        pool.append({
            "match":            match_str,
            "pick":             best["pick"],
            "prob":             round(best["prob"], 1),
            "raw_prob":         round(raw_p, 1),
            "league":           league,
            "confidence":       conf,
            "market_type":      best["market_type"],
            "sport_emoji":      emoji,
            "risk_reasons":     risk_reasons,
            "calibration_note": cal_note,
        })

    # ── Step 2: Sort by probability descending ────────────────────────────
    pool.sort(key=lambda x: x["prob"], reverse=True)

    # ── Step 3: Apply variety constraints ─────────────────────────────────
    # Allow at most _MAX_SAME_MARKET legs of the same market type.
    # Only one leg per match (deduplication by match name).
    selected: list = []
    market_counts: dict = {}
    used_matches: set = set()

    for leg in pool:
        if len(selected) >= max_legs:
            break
        mtype = leg["market_type"]
        match_name = leg["match"]
        if match_name in used_matches:
            continue
        if market_counts.get(mtype, 0) >= _MAX_SAME_MARKET:
            continue
        market_counts[mtype] = market_counts.get(mtype, 0) + 1
        used_matches.add(match_name)
        selected.append(leg)

    return selected


# ── Parlay tiers ───────────────────────────────────────────────────────────────

def build_parlays(legs: list) -> dict:
    """
    Build 3 risk-tiered parlays from sorted legs.

    Tiers:
      SAFE     (🟢) — top 2 legs
      BALANCED (🟡) — top 3 legs
      RISKY    (🔴) — top 4-5 legs

    Combined probability = (p1/100) * (p2/100) * ... * 100

    Parameters
    ----------
    legs : sorted list of leg dicts (output of generate_parlay_legs)

    Returns
    -------
    {
        "safe":     {"legs": [...], "combined_prob": float} or None,
        "balanced": {"legs": [...], "combined_prob": float} or None,
        "risky":    {"legs": [...], "combined_prob": float} or None,
    }
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


# ── Telegram formatter ─────────────────────────────────────────────────────────

def format_parlay(
    parlays: dict,
    filtered_count: int = 0,
    parlay_id: str = "",
) -> str:
    """
    Format the parlay output for Telegram.

    Returns Markdown-compatible string with box-style layout.
    Includes sport emoji and market type in each leg.

    Parameters
    ----------
    parlays       : output of ``build_parlays``
    filtered_count: number of matches excluded for high risk / low confidence
    parlay_id     : unique ID returned by ``parlay_history.save_parlay``
                    (shown at the bottom so the user can report results)
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

    any_calibrated = False  # track whether any calibration note exists

    for key, emoji, label, _ in tiers:
        tier = parlays.get(key)
        if not tier:
            continue

        tier_legs = tier["legs"]
        n_patas = len(tier_legs)
        lines.append(f"{emoji} *{label}* ({n_patas} patas)")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for i, leg in enumerate(tier_legs):
            num = _numbers[i] if i < len(_numbers) else f"{i + 1}."
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

    if filtered_count > 0:
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
