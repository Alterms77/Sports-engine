import math
import random


# ----------------------------
# POISSON DISTRIBUTION
# ----------------------------

def poisson_pmf(k, lambd):
    """
    Probabilidad de k eventos dado lambda
    """
    return (lambd**k * math.exp(-lambd)) / math.factorial(k)


def poisson_sample(lambd):
    """
    Genera una muestra usando método de Knuth
    """
    L = math.exp(-lambd)
    k = 0
    p = 1

    while p > L:
        k += 1
        p *= random.random()

    return k - 1


# ----------------------------
# NORMAL DISTRIBUTION
# ----------------------------

def normal_sample(mean, std_dev):
    return random.gauss(mean, std_dev)