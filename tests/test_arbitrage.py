"""
Tests for core.arbitrage — Surebet / Arbitrage Detector.
"""
import pytest
from core.arbitrage import (
    find_arbitrage,
    format_arb_alert,
    ArbitrageAlert,
    ArbLeg,
    MIN_ARB_MARGIN_PCT,
)
from core.market_scanner import BookmakerOdds


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _books(pairs):
    """Build a List[BookmakerOdds] from [(bookmaker, odds), ...] pairs."""
    return [BookmakerOdds(bookmaker=bk, odds=od) for bk, od in pairs]


# ─────────────────────────────────────────────────────────────────────────────
# find_arbitrage
# ─────────────────────────────────────────────────────────────────────────────

class TestFindArbitrage:

    def test_clear_arb_two_outcomes(self):
        """2-outcome market with clear arb: 1/2.10 + 1/2.10 < 1."""
        outcome_odds = {
            "Victoria Home": _books([("Bet365", 2.10), ("Caliente", 1.95)]),
            "Victoria Away": _books([("Pinnacle", 2.10), ("Betway", 1.88)]),
        }
        arb = find_arbitrage(outcome_odds)
        # Best: Home 2.10 + Away 2.10 → implied = 0.476 + 0.476 = 0.952 → margin 4.8%
        assert arb is not None
        assert arb.margin_pct > 0
        assert len(arb.legs) == 2

    def test_no_arb_three_outcomes(self):
        """Standard 1X2 where no arb exists."""
        outcome_odds = {
            "Victoria Home": _books([("Bet365", 1.90)]),
            "Empate":        _books([("Bet365", 3.40)]),
            "Victoria Away": _books([("Bet365", 4.20)]),
        }
        # implied = 0.526 + 0.294 + 0.238 = 1.058 → no arb
        arb = find_arbitrage(outcome_odds)
        assert arb is None

    def test_arb_three_outcomes(self):
        """Three outcomes where best odds across different books form an arb."""
        outcome_odds = {
            "Victoria Home": _books([("BookA", 3.20)]),
            "Empate":        _books([("BookB", 3.20)]),
            "Victoria Away": _books([("BookC", 3.20)]),
        }
        # implied = 3 * (1/3.20) = 0.9375 → margin 6.25%
        arb = find_arbitrage(outcome_odds)
        assert arb is not None
        assert arb.margin_pct == pytest.approx(6.25, abs=0.01)
        assert len(arb.legs) == 3

    def test_stake_distribution_sums_to_one(self):
        """Stakes across all legs should sum to 1 (representing 100% of bank)."""
        outcome_odds = {
            "Home": _books([("A", 2.10)]),
            "Away": _books([("B", 2.10)]),
        }
        arb = find_arbitrage(outcome_odds)
        assert arb is not None
        total_stake = sum(leg.stake_pct for leg in arb.legs)
        assert total_stake == pytest.approx(1.0, abs=1e-6)

    def test_below_min_margin_not_returned(self):
        """An arb just at or below MIN_ARB_MARGIN_PCT should be ignored."""
        # Craft odds where margin ≈ 0.1% (below 0.3% floor)
        # 1/2.00 + 1/2.00 = 1.00 → margin = 0
        outcome_odds = {
            "Home": _books([("A", 2.00)]),
            "Away": _books([("B", 2.00)]),
        }
        arb = find_arbitrage(outcome_odds)
        # margin = 0 → below MIN_ARB_MARGIN_PCT → None
        assert arb is None

    def test_single_outcome_returns_none(self):
        """A single-outcome market cannot have arb."""
        outcome_odds = {
            "Home": _books([("A", 1.80), ("B", 1.85)]),
        }
        assert find_arbitrage(outcome_odds) is None

    def test_empty_odds_returns_none(self):
        """Outcomes with no valid odds should not produce arb."""
        outcome_odds = {
            "Home": [],
            "Away": _books([("B", 2.10)]),
        }
        assert find_arbitrage(outcome_odds) is None

    def test_metadata_passed_through(self):
        """sport, event, market_group should appear in the alert."""
        outcome_odds = {
            "Home": _books([("A", 2.10)]),
            "Away": _books([("B", 2.10)]),
        }
        arb = find_arbitrage(
            outcome_odds,
            sport="NBA",
            event="Lakers vs Celtics",
            market_group="h2h",
        )
        assert arb is not None
        assert arb.sport == "NBA"
        assert arb.event == "Lakers vs Celtics"
        assert arb.market_group == "h2h"

    def test_best_odds_used_per_outcome(self):
        """Only the best available odds per outcome should be used."""
        outcome_odds = {
            "Home": _books([("A", 1.80), ("B", 2.30)]),  # best = B @ 2.30
            "Away": _books([("C", 1.90), ("D", 2.30)]),  # best = D @ 2.30
        }
        arb = find_arbitrage(outcome_odds)
        assert arb is not None
        best_home = next(l for l in arb.legs if "Home" in l.outcome)
        best_away = next(l for l in arb.legs if "Away" in l.outcome)
        assert best_home.odds == 2.30
        assert best_away.odds == 2.30


# ─────────────────────────────────────────────────────────────────────────────
# format_arb_alert
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatArbAlert:

    def _make_alert(self):
        return ArbitrageAlert(
            sport="NBA",
            event="Lakers vs Celtics",
            market_group="h2h",
            margin_pct=4.8,
            legs=[
                ArbLeg("Victoria Lakers", "Bet365", 2.10, 0.476),
                ArbLeg("Victoria Celtics", "Pinnacle", 2.10, 0.476),
            ],
        )

    def test_format_returns_string(self):
        alert = self._make_alert()
        text = format_arb_alert(alert)
        assert isinstance(text, str)

    def test_format_contains_keywords(self):
        alert = self._make_alert()
        text = format_arb_alert(alert)
        assert "SUREBET" in text or "ARBITRAJE" in text
        assert "Lakers vs Celtics" in text
        assert "4.8" in text  # margin

    def test_format_mentions_all_legs(self):
        alert = self._make_alert()
        text = format_arb_alert(alert)
        assert "Lakers" in text
        assert "Celtics" in text
        assert "Bet365" in text
        assert "Pinnacle" in text
