import math


def expected_corners(xg_home, xg_away, league="default"):
    """
    Calculate expected corners per team using possession dominance model.

    Key insight: Teams with higher xG tend to have more possession and
    thus more corners. The team with MORE xG gets proportionally more corners.

    Real-world averages by league (2023-24 data):
    - Premier League: avg 10.7 corners/match (home 5.6, away 5.1)
    - La Liga: avg 10.1 corners/match
    - Liga MX: avg 9.8 corners/match
    - Champions League: avg 10.9 corners/match
    - Serie A: avg 10.3 corners/match
    - Bundesliga: avg 10.5 corners/match
    """
    LEAGUE_CORNERS = {
        "premier_league": 10.7,
        "Premier League": 10.7,
        "la_liga": 10.1,
        "La Liga": 10.1,
        "liga_mx": 9.8,
        "Liga MX": 9.8,
        "champions_league": 10.9,
        "Champions League": 10.9,
        "serie_a": 10.3,
        "Serie A": 10.3,
        "bundesliga": 10.5,
        "Bundesliga": 10.5,
        "ligue_1": 10.2,
        "Ligue 1": 10.2,
        "default": 10.3,
    }

    base = LEAGUE_CORNERS.get(league, LEAGUE_CORNERS["default"])
    total_xg = xg_home + xg_away
    league_avg_xg = 2.65  # average total xG per match

    # Scale corners by how attacking the match is vs average
    # More goals expected = more corners (correlation ~0.35 in real data)
    xg_factor = 1.0 + (total_xg - league_avg_xg) * 0.18
    total_corners = base * max(xg_factor, 0.7)

    # Split between teams based on xG dominance
    # The team attacking more gets more corners
    if total_xg > 0:
        home_share = (xg_home / total_xg) * 0.8 + 0.1  # slight home bias
        # Home advantage factor: home teams get ~0.3 extra corners
        home_corners = total_corners * home_share + 0.3
        away_corners = total_corners * (1 - home_share) - 0.3
    else:
        home_corners = total_corners * 0.52
        away_corners = total_corners * 0.48

    return {
        "home": round(max(home_corners, 2.0), 1),
        "away": round(max(away_corners, 2.0), 1),
        "total": round(max(home_corners, 2.0) + max(away_corners, 2.0), 1),
    }


def corners_market(corners_data, line=9.5):
    """Market suggestion with probability estimate."""
    total = corners_data["total"]
    # Rough probability estimate based on distance from line
    diff = total - line
    # Sigmoid-like probability
    prob_over = round(1 / (1 + math.exp(-diff * 0.5)) * 100, 1)

    return {
        "home": corners_data["home"],
        "away": corners_data["away"],
        "total": total,
        "line": line,
        "over_prob": prob_over,
        "under_prob": round(100 - prob_over, 1),
        "suggestion": "Over" if total > line else "Under",
    }
