def confidence_level(probs: dict) -> str:
    """
    Determine confidence based only on the dominant 1X2 probability.

    Thresholds (football-specific):
      ALTA  — main_prob >= 55%  (very strong signal in football)
      MEDIA — main_prob >= 42%
      BAJA  — below 42%
    """
    home = probs.get("home_win", 0)
    draw = probs.get("draw", 0)
    away = probs.get("away_win", 0)

    main_prob = max(home, draw, away)

    if main_prob >= 55:
        return "ALTA"
    elif main_prob >= 42:
        return "MEDIA"
    else:
        return "BAJA"
