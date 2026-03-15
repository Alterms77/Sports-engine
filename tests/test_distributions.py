"""
Tests for core/distributions.py

Covers:
  - poisson_pmf: basic values, edge cases (lambda=0, large lambda)
  - poisson_sample: distribution shape (statistical, seeded)
"""
import math
import random

import pytest


# ── Import under test ─────────────────────────────────────────────────────────
from core.distributions import poisson_pmf, poisson_sample


# ─────────────────────────────────────────────────────────────────────────────
# poisson_pmf
# ─────────────────────────────────────────────────────────────────────────────

class TestPoissonPmf:
    def test_lambda_zero_k_zero(self):
        """P(0; 0) = 1 by convention."""
        assert poisson_pmf(0, 0) == 1.0

    def test_lambda_zero_k_positive(self):
        """P(k>0; 0) = 0."""
        assert poisson_pmf(1, 0) == 0.0
        assert poisson_pmf(5, 0) == 0.0

    def test_known_values(self):
        """P(k; λ) for known values, tolerance ±0.0001."""
        # P(0; 1.5) = exp(-1.5) ≈ 0.2231
        assert abs(poisson_pmf(0, 1.5) - math.exp(-1.5)) < 1e-9

        # P(1; 1.5) = 1.5 * exp(-1.5) ≈ 0.3347
        expected = 1.5 * math.exp(-1.5)
        assert abs(poisson_pmf(1, 1.5) - expected) < 1e-9

        # P(2; 1.5) = (1.5^2 / 2!) * exp(-1.5) ≈ 0.2510
        expected = (1.5 ** 2 / 2) * math.exp(-1.5)
        assert abs(poisson_pmf(2, 1.5) - expected) < 1e-9

    def test_probabilities_sum_to_one(self):
        """Sum P(k; λ) for k=0..20 should be very close to 1."""
        for lambd in (0.5, 1.0, 1.5, 2.0, 3.0):
            total = sum(poisson_pmf(k, lambd) for k in range(21))
            assert abs(total - 1.0) < 0.001, f"Sum={total} for λ={lambd}"

    def test_large_lambda_normal_approx(self):
        """For λ=35 the normal approximation branch is used; PMF at mean should be positive."""
        # Just verify it returns a sensible probability
        p = poisson_pmf(35, 35)
        assert 0 < p < 1

    def test_non_negative_output(self):
        """PMF is always non-negative."""
        for k in range(10):
            for lam in (0.1, 1.0, 5.0):
                assert poisson_pmf(k, lam) >= 0


# ─────────────────────────────────────────────────────────────────────────────
# poisson_sample
# ─────────────────────────────────────────────────────────────────────────────

class TestPoissonSample:
    def test_lambda_zero_returns_zero(self):
        for _ in range(20):
            assert poisson_sample(0) == 0

    def test_non_negative(self):
        random.seed(42)
        for lam in (0.5, 1.5, 5.0, 35.0):
            for _ in range(50):
                assert poisson_sample(lam) >= 0

    def test_mean_approximation(self):
        """Sample mean should be close to λ over many trials."""
        random.seed(0)
        n = 5_000
        for lam in (0.8, 1.5, 3.0):
            samples = [poisson_sample(lam) for _ in range(n)]
            mean = sum(samples) / n
            # Allow ±15% tolerance
            assert abs(mean - lam) / lam < 0.15, f"mean={mean:.3f}, λ={lam}"

    def test_large_lambda_mean(self):
        """Large λ takes normal-approx path; mean should still be close."""
        random.seed(1)
        n = 2_000
        lam = 50.0
        samples = [poisson_sample(lam) for _ in range(n)]
        mean = sum(samples) / n
        assert abs(mean - lam) < 3.0, f"mean={mean:.2f}, λ={lam}"
