import math

def poisson_prob(lmbda, k):
    return (math.exp(-lmbda) * lmbda ** k) / math.factorial(k)

def scoreline_matrix(xg_home, xg_away, max_goals=5):
    results = {}

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_prob(xg_home, h) * poisson_prob(xg_away, a)
            results[f"{h}-{a}"] = round(p * 100, 2)

    return results

def top_scorelines(xg_home, xg_away, top_n=5):
    matrix = scoreline_matrix(xg_home, xg_away)
    ordered = sorted(matrix.items(), key=lambda x: x[1], reverse=True)
    return ordered[:top_n]
