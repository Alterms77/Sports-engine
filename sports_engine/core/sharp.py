"""
Sharp Game Detector.

A game is "sharp" when multiple model signals converge on a clear edge:
  - Large xG differential (>0.8)
  - High confidence (>=55%)
  - Strong form alignment (winning streak + good xT)
  - Elo advantage (>150 points)

Sharp games are marked with 🔱 SHARP GAME in the output.
"""


def detect_sharp_game(
    prediction: dict,
    home_elo: float = 1500,
    away_elo: float = 1500,
) -> dict:
    """
    Detect whether a match has a clear exploitable statistical edge.

    Parameters
    ----------
    prediction  : full prediction dict from predict_match()
    home_elo    : Elo rating for the home team
    away_elo    : Elo rating for the away team

    Returns
    -------
    {
        "is_sharp": bool,
        "reasons": list[str],
        "edge_score": float,
        "pick": str,
        "pick_prob": float,
    }
    """
    reasons = []
    edge_score = 0.0

    xg_home = prediction.get("xg_home", 0.0)
    xg_away = prediction.get("xg_away", 0.0)
    xg_diff = abs(xg_home - xg_away)

    home_win = prediction.get("home_win", 0.0)
    draw = prediction.get("draw", 0.0)
    away_win = prediction.get("away_win", 0.0)
    over_2_5 = prediction.get("over_2_5", 0.0)
    cs_home = prediction.get("clean_sheet_home")
    cs_away = prediction.get("clean_sheet_away")

    # Determine the favored side
    if home_win >= away_win:
        main_prob = home_win
        favored = "home"
    else:
        main_prob = away_win
        favored = "away"

    # Elo difference favoring the stronger side
    if favored == "home":
        elo_diff = home_elo - away_elo
    else:
        elo_diff = away_elo - home_elo

    # ── Scoring criteria ──

    if xg_diff > 0.8:
        edge_score += 1.0
        reasons.append(f"Gran diferencial xG ({xg_home:.2f} vs {xg_away:.2f})")
    if xg_diff > 1.2:
        edge_score += 0.5
        reasons.append("Diferencial xG muy alto (>1.2)")

    if main_prob >= 55:
        edge_score += 1.0
        reasons.append(f"Alta probabilidad del favorito ({main_prob:.1f}%)")
    if main_prob >= 65:
        edge_score += 0.5
        reasons.append("Probabilidad muy alta del favorito (≥65%)")

    # Clean sheet probability
    cs_prob = cs_home if favored == "home" else cs_away
    if cs_prob is not None and cs_prob > 0.35:
        edge_score += 0.5
        reasons.append(f"Alta prob. portería a cero ({cs_prob*100:.0f}%)")

    if elo_diff > 150:
        edge_score += 0.5
        reasons.append(f"Ventaja Elo significativa (+{elo_diff:.0f})")
    if elo_diff > 250:
        edge_score += 0.5
        reasons.append(f"Ventaja Elo muy grande (+{elo_diff:.0f})")

    # Clear over/under signal
    if over_2_5 > 65 or over_2_5 < 35:
        edge_score += 0.5
        direction = "Over" if over_2_5 > 65 else "Under"
        reasons.append(f"Señal clara de goles: {direction} 2.5 ({over_2_5:.1f}%)")

    # Form emoji check
    form_home = prediction.get("form_home", {})
    form_away = prediction.get("form_away", {})
    favored_form = form_home if favored == "home" else form_away
    if favored_form.get("emoji") == "🔥":
        edge_score += 0.5
        reasons.append("El favorito está en racha de fuego 🔥")

    # ── Determine pick ──
    home_name = prediction.get("home", "Local")
    away_name = prediction.get("away", "Visitante")

    win_to_nil = prediction.get("win_to_nil")
    if win_to_nil and win_to_nil.get("high_value"):
        pick = f"Victoria a cero {win_to_nil['team']}"
        pick_prob = (cs_home or 0) * home_win / 100 * 100 if favored == "home" else (cs_away or 0) * away_win / 100 * 100
        if pick_prob <= 0:
            pick_prob = main_prob * 0.6
    elif over_2_5 > 65:
        pick = "Over 2.5"
        pick_prob = over_2_5
    elif favored == "home":
        pick = f"Victoria {home_name}"
        pick_prob = home_win
    else:
        pick = f"Victoria {away_name}"
        pick_prob = away_win

    return {
        "is_sharp": edge_score >= 3.0,
        "reasons": reasons,
        "edge_score": round(edge_score, 1),
        "pick": pick,
        "pick_prob": round(pick_prob, 1),
    }
