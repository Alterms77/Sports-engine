from core.distributions import poisson_pmf


def scoreline_matrix(xg_home: float, xg_away: float, max_goals: int = 5) -> dict:
    results = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(h, xg_home) * poisson_pmf(a, xg_away)
            results[f"{h}-{a}"] = round(p * 100, 2)
    return results


def top_scorelines(xg_home: float, xg_away: float, top_n: int = 5) -> list:
    matrix = scoreline_matrix(xg_home, xg_away)
    ordered = sorted(matrix.items(), key=lambda x: x[1], reverse=True)
    return ordered[:top_n]

