import random
import math


# ===============================
# DISTRIBUCIÓN POISSON
# ===============================
def poisson(lmbda):

    L = math.exp(-lmbda)
    k = 0
    p = 1

    while p > L:
        k += 1
        p *= random.random()

    return k - 1


# ===============================
# SIMULACIÓN MONTE CARLO
# ===============================
def simulate_scoreline(xg_home, xg_away, simulations=10000):

    home_goals = []
    away_goals = []

    home_wins = 0
    draws = 0
    away_wins = 0

    for _ in range(simulations):

        hg = poisson(xg_home)
        ag = poisson(xg_away)

        home_goals.append(hg)
        away_goals.append(ag)

        if hg > ag:
            home_wins += 1
        elif hg == ag:
            draws += 1
        else:
            away_wins += 1

    return {

        "avg_home_goals": round(sum(home_goals) / simulations, 2),
        "avg_away_goals": round(sum(away_goals) / simulations, 2),

        "home_win_prob": round(home_wins / simulations * 100, 2),
        "draw_prob": round(draws / simulations * 100, 2),
        "away_win_prob": round(away_wins / simulations * 100, 2)
    }    
