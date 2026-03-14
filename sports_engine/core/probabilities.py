# core/probabilities.py

from core.distributions import poisson_pmf


def match_probabilities(xg_home: float, xg_away: float, max_goals: int = 6) -> dict:
    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    over_1_5 = 0.0
    over_2_5 = 0.0
    over_3_5 = 0.0
    btts = 0.0

    for hg in range(max_goals + 1):
        for ag in range(max_goals + 1):
            p = poisson_pmf(hg, xg_home) * poisson_pmf(ag, xg_away)

            # 1X2
            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p

            # Over
            total_goals = hg + ag
            if total_goals > 1:
                over_1_5 += p
            if total_goals > 2:
                over_2_5 += p
            if total_goals > 3:
                over_3_5 += p

            # BTTS
            if hg > 0 and ag > 0:
                btts += p

    return {
        "home_win": round(home_win * 100, 1),
        "draw": round(draw * 100, 1),
        "away_win": round(away_win * 100, 1),
        "over_1_5": round(over_1_5 * 100, 1),
        "over_2_5": round(over_2_5 * 100, 1),
        "over_3_5": round(over_3_5 * 100, 1),
        "btts": round(btts * 100, 1),
    }
