from core.distributions import poisson_sample
from core.config import MONTE_CARLO_SIMULATIONS


# ===============================
# SIMULACIÓN MONTE CARLO
# ===============================
def simulate_scoreline(
    xg_home: float,
    xg_away: float,
    simulations: int = MONTE_CARLO_SIMULATIONS,
) -> dict:
    """
    Monte Carlo simulation using the shared Poisson sampler.
    Uses MONTE_CARLO_SIMULATIONS (default 50,000) for stable probabilities.
    """
    home_goals_total = 0
    away_goals_total = 0

    home_wins = 0
    draws = 0
    away_wins = 0

    for _ in range(simulations):
        hg = poisson_sample(xg_home)
        ag = poisson_sample(xg_away)

        home_goals_total += hg
        away_goals_total += ag

        if hg > ag:
            home_wins += 1
        elif hg == ag:
            draws += 1
        else:
            away_wins += 1

    return {
        "avg_home_goals": round(home_goals_total / simulations, 2),
        "avg_away_goals": round(away_goals_total / simulations, 2),
        "home_win_prob": round(home_wins / simulations * 100, 2),
        "draw_prob": round(draws / simulations * 100, 2),
        "away_win_prob": round(away_wins / simulations * 100, 2),
    }

