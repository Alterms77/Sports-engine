def confidence_level(probs):
    numeric_probs = [
        v for v in probs.values()
        if isinstance(v, (int, float))
    ]

    if not numeric_probs:
        return "BAJA"

    main_prob = max(numeric_probs)

    if main_prob >= 70:
        return "ALTA"
    elif main_prob >= 55:
        return "MEDIA"
    else:
        return "BAJA"
