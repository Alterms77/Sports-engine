"""
Betting Intelligence Engine — Smart market analysis for Sports Engine.

Provides:
  - Kelly Criterion stake sizing (fractional Kelly, default 25%)
  - Expected Value (EV) for any market
  - Bookmaker margin (overround) calculation
  - Fair/no-vig odds computation
  - Multi-market ranking by EV
  - Suggested bet size in units
"""

from typing import Dict, List


# ─────────────────────────────────────────────────
# CORE CALCULATIONS
# ─────────────────────────────────────────────────

def fair_odds(prob_pct: float) -> float:
    """Convert a probability (%) to decimal fair odds with no margin."""
    if prob_pct <= 0:
        return 0.0
    return round(100.0 / prob_pct, 3)


def bookmaker_margin(odds_list: List[float]) -> float:
    """
    Calculate bookmaker's overround (margin %) from a list of decimal odds.

    margin = (sum of implied probabilities − 1.0) × 100
    E.g., [1.8, 3.5, 4.5] → ~6.3% margin
    """
    if not odds_list:
        return 0.0
    overround = sum(1.0 / o for o in odds_list if o > 1.0)
    return round((overround - 1.0) * 100, 2)


def implied_prob(decimal_odds: float) -> float:
    """Raw implied probability from decimal odds (includes margin)."""
    if decimal_odds <= 0:
        return 0.0
    return round(100.0 / decimal_odds, 2)


def no_vig_prob(raw_odds: float, overround: float) -> float:
    """
    Remove bookmaker margin to get fair implied probability.

    overround is the sum of all implied probabilities (e.g. 1.063 for 6.3% margin).
    Returns fair probability as a percentage.
    """
    if raw_odds <= 0 or overround <= 0:
        return 0.0
    return round((1.0 / raw_odds) / overround * 100, 2)


def expected_value(model_prob_pct: float, decimal_odds: float) -> float:
    """
    Compute Expected Value of a bet as percentage of stake.

    EV = (model_prob × decimal_odds) − 1  [expressed as %]
    Positive EV = value bet.
    """
    if decimal_odds <= 0:
        return 0.0
    prob = model_prob_pct / 100.0
    ev = (prob * decimal_odds - 1.0) * 100.0
    return round(ev, 2)


def kelly_fraction(
    model_prob_pct: float,
    decimal_odds: float,
    fraction: float = 0.25,
) -> float:
    """
    Compute fractional Kelly stake as % of bankroll.

    Full Kelly: f = (b×p − q) / b   where b = odds−1, p = win_prob, q = 1−p
    We use fractional Kelly (default 25%) to reduce variance.
    Returns 0.0 if the bet has no edge.
    """
    if decimal_odds <= 1.0:
        return 0.0
    p = model_prob_pct / 100.0
    q = 1.0 - p
    b = decimal_odds - 1.0
    full_kelly = (b * p - q) / b
    stake_pct  = full_kelly * fraction * 100.0
    return round(max(stake_pct, 0.0), 2)


# ─────────────────────────────────────────────────
# MARKET ANALYSIS
# ─────────────────────────────────────────────────

def analyze_betting_markets(
    prediction: Dict,
    odds: Dict,
) -> Dict:
    """
    Full betting intelligence analysis for a match.

    Parameters
    ----------
    prediction : full prediction dict from predict_match()
    odds       : {
        "home": float, "draw": float, "away": float,  ← main 1X2
        "over_2_5": float, "btts": float,              ← optional extra markets
    }

    Returns
    -------
    {
        "margin":   float,   # bookmaker margin %
        "markets": [
            {
                "name": str,
                "model_prob": float,    # model probability %
                "bookie_odds": float,   # given decimal odds
                "fair_odds": float,     # no-vig odds
                "bookie_prob": float,   # raw implied prob %
                "ev": float,            # expected value %
                "kelly": float,         # fractional Kelly % of bankroll
                "verdict": str,         # emoji verdict
            }, ...
        ]
    }
    """
    home_name = prediction.get("home", "Local")
    away_name = prediction.get("away", "Visitante")

    market_map = {
        "home":    (f"Victoria {home_name}", prediction.get("home_win",  0)),
        "draw":    ("Empate",                prediction.get("draw",      0)),
        "away":    (f"Victoria {away_name}", prediction.get("away_win",  0)),
        "over_2_5": ("Over 2.5",             prediction.get("over_2_5",  0)),
        "btts":    ("Ambos Marcan",           prediction.get("btts",      0)),
    }

    # Compute overround from the 1X2 prices supplied
    main_prices = [
        odds.get(k, 0) for k in ("home", "draw", "away") if odds.get(k, 0) > 1.0
    ]
    overround = sum(1.0 / o for o in main_prices) if main_prices else 1.0
    margin    = bookmaker_margin(main_prices)

    markets = []
    for key, (name, model_prob) in market_map.items():
        bookie_odds = odds.get(key, 0)
        if bookie_odds <= 1.0:
            continue

        f_odds      = fair_odds(model_prob)
        bookie_prob = no_vig_prob(bookie_odds, overround) if overround > 0 else implied_prob(bookie_odds)
        ev          = expected_value(model_prob, bookie_odds)
        kelly       = kelly_fraction(model_prob, bookie_odds)

        if ev >= 8.0:
            verdict = "🔥 VALOR MUY ALTO"
        elif ev >= 3.0:
            verdict = "✅ VALOR"
        elif ev >= 0:
            verdict = "⚠️ JUSTO"
        else:
            verdict = "❌ EVITAR"

        markets.append({
            "name":        name,
            "model_prob":  round(model_prob, 1),
            "bookie_odds": bookie_odds,
            "fair_odds":   f_odds,
            "bookie_prob": bookie_prob,
            "ev":          ev,
            "kelly":       kelly,
            "verdict":     verdict,
        })

    # Sort by EV descending
    markets.sort(key=lambda x: x["ev"], reverse=True)

    return {
        "margin":  margin,
        "markets": markets,
    }


def format_betting_intelligence(analysis: Dict, prediction: Dict) -> str:
    """
    Format betting intelligence analysis for Telegram.

    Parameters
    ----------
    analysis   : output of analyze_betting_markets()
    prediction : full prediction dict (for team names)
    """
    home = prediction.get("home", "Local")
    away = prediction.get("away", "Visitante")
    margin = analysis.get("margin", 0)

    lines = [
        f"╔══════════════════════════════════╗",
        f"  💰 BETTING INTELLIGENCE",
        f"  {home} vs {away}",
        f"╚══════════════════════════════════╝",
        "",
        f"📐 Margen casas de apuestas: `{margin:.1f}%`",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    markets = analysis.get("markets", [])
    if not markets:
        lines.append("⚠️ No se proporcionaron cuotas para analizar.")
        return "\n".join(lines)

    for m in markets:
        ev_str = f"+{m['ev']:.1f}%" if m["ev"] >= 0 else f"{m['ev']:.1f}%"
        kelly_str = f"`{m['kelly']:.1f}%` bankroll" if m["kelly"] > 0 else "—"
        lines += [
            f"*{m['name']}*",
            f"  Prob. modelo: `{m['model_prob']:.1f}%`  Cuota justa: `{m['fair_odds']}`",
            f"  Cuota ofrecida: `{m['bookie_odds']}`  ({ev_str} EV)  {m['verdict']}",
            f"  Kelly: {kelly_str}",
            "",
        ]

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("_⚠️ Kelly es referencial. Apuesta con responsabilidad._")
    return "\n".join(lines)
