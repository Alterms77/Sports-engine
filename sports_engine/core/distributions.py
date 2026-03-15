import math
import random


# ----------------------------
# POISSON DISTRIBUTION
# ----------------------------

def poisson_pmf(k: int, lambd: float) -> float:
    """Probability of exactly k events given Poisson rate lambda."""
    if lambd <= 0:
        return 1.0 if k == 0 else 0.0
    return (lambd ** k * math.exp(-lambd)) / math.factorial(k)


def poisson_sample(lambd: float) -> int:
    """
    Draw a single integer sample from Poisson(lambda) using Knuth's algorithm.
    For large lambda (>30) falls back to a rounded normal approximation for speed.
    """
    if lambd <= 0:
        return 0
    if lambd > 30:
        # Normal approximation for performance when λ > 30 (provides reasonable
        # accuracy; exact Poisson rarely exceeds λ = 30 in football contexts)
        return max(0, round(random.gauss(lambd, math.sqrt(lambd))))
    L = math.exp(-lambd)
    k = 0
    p = 1.0
    while p > L:
        k += 1
        p *= random.random()
    return k - 1


# ----------------------------
# NORMAL DISTRIBUTION
# ----------------------------

def normal_sample(mean: float, std_dev: float) -> float:
    return random.gauss(mean, std_dev)
