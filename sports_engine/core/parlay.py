"""
Parlay (Combinada) Generator.

Builds reliable multi-leg parlays from the day's matches by:
1. Running predictions for all today's matches
2. Filtering to only ALTA/MEDIA confidence picks
3. Selecting the best 2-5 legs with highest individual probabilities
4. Computing combined probability and suggested stake
5. Offering different risk tiers: SAFE (2 legs), BALANCED (3 legs), RISKY (4-5 legs)

Each leg is a single market pick (1X2, Over/Under, BTTS).
"""

_CONFIDENCE_RANK = {"ALTA": 2, "MEDIA": 1, "BAJA": 0}


def generate_parlay_legs(
    predictions: list,
    max_legs: int = 5,
    min_confidence: str = "MEDIA",
) -> list:
    """
    Generate individual parlay legs from a list of predictions.

    Parameters
    ----------
    predictions     : list of prediction dicts from predict_match()
    max_legs        : maximum number of legs to return
    min_confidence  : minimum confidence level to include ("ALTA" or "MEDIA")

    Returns
    -------
    list of dicts, each: {
        "match": str,
        "pick": str,
        "prob": float,
        "league": str,
        "confidence": str,
    }
    Sorted by prob descending, limited to max_legs.
    """
    min_rank = _CONFIDENCE_RANK.get(min_confidence, 1)
    legs = []

    for pred in predictions:
        conf = pred.get("confidence", "BAJA")
        if _CONFIDENCE_RANK.get(conf, 0) < min_rank:
            continue

        home = pred.get("home", "Local")
        away = pred.get("away", "Visitante")
        league = pred.get("league", "")
        match_str = f"{home} vs {away}"

        # Collect candidate picks with their probabilities
        candidates = [
            (f"Victoria {home}", pred.get("home_win", 0)),
            (f"Victoria {away}", pred.get("away_win", 0)),
            (f"Over 1.5", pred.get("over_1_5", 0)),
            (f"Over 2.5", pred.get("over_2_5", 0)),
            (f"Ambos Marcan (BTTS)", pred.get("btts", 0)),
        ]

        # Pick the single strongest market with prob >= 55%
        best_pick, best_prob = max(candidates, key=lambda x: x[1])
        if best_prob < 55:
            continue

        legs.append({
            "match": match_str,
            "pick": best_pick,
            "prob": round(best_prob, 1),
            "league": league,
            "confidence": conf,
        })

    # Sort by probability descending, take top max_legs
    legs.sort(key=lambda x: x["prob"], reverse=True)
    return legs[:max_legs]


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


def format_parlay(parlays: dict) -> str:
    """
    Format the parlay output for Telegram.

    Returns Markdown-compatible string with box-style layout.
    """
    lines = [
        "╔══════════════════════════════════╗",
        "  🎰 PARLAY DEL DÍA — Sports Engine",
        "╚══════════════════════════════════╝",
        "",
    ]

    tiers = [
        ("safe",     "🟢", "SEGURA",      "2 patas"),
        ("balanced", "🟡", "BALANCEADA",  "3 patas"),
        ("risky",    "🔴", "ARRIESGADA",  "4+ patas"),
    ]

    _numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    for key, emoji, label, patas in tiers:
        tier = parlays.get(key)
        if not tier:
            continue

        tier_legs = tier["legs"]
        n_patas = len(tier_legs)
        lines.append(f"{emoji} *{label}* ({n_patas} patas)")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for i, leg in enumerate(tier_legs):
            num = _numbers[i] if i < len(_numbers) else f"{i+1}."
            lines.append(
                f"  {num} {leg['match']} → {leg['pick']} ({leg['prob']}%)"
            )
        lines.append(f"📊 Prob. combinada: *{tier['combined_prob']}%*")
        lines.append("")

    if not any(parlays.get(k) for k in ("safe", "balanced", "risky")):
        lines.append("⚠️ No hay suficientes picks confiables para armar parlay hoy.")
        lines.append("")

    lines.append("⚠️ _Las parlays son recreativas. Apuesta responsablemente._")
    return "\n".join(lines)
