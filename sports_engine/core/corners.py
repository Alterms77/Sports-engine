def expected_corners(xg_home, xg_away):
    total_xg = xg_home + xg_away
    corners = 8 + total_xg * 2.3
    return round(corners, 1)


def corners_market(expected, line=9.5):
    return {
        "expected": expected,
        "line": line,
        "suggestion": "Over" if expected > line else "Under"
    }
