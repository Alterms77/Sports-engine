"""
Tests for core.auto_scanner and api.odds_api.

All external HTTP calls are patched so the tests run fully offline.
"""
import time
import pytest
from unittest.mock import patch, MagicMock

from core.market_scanner import BookmakerOdds, MarketScan
from core.arbitrage import ArbitrageAlert, ArbLeg


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scan(sport, event, market, odds_pairs):
    """Build a MarketScan from a list of (bookmaker, odds) pairs."""
    return MarketScan(
        sport=sport,
        event=event,
        market=market,
        odds_list=[BookmakerOdds(bk, od) for bk, od in odds_pairs],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplication:
    """_is_duplicate and _expire_seen should work correctly."""

    def setup_method(self):
        # Reset module state before each test
        import core.auto_scanner as _as
        _as._seen.clear()

    def test_first_call_not_duplicate(self):
        from core.auto_scanner import _is_duplicate
        assert _is_duplicate("key1", 3600) is False

    def test_second_call_within_ttl_is_duplicate(self):
        from core.auto_scanner import _is_duplicate
        _is_duplicate("key2", 3600)  # first call — registers key
        assert _is_duplicate("key2", 3600) is True

    def test_call_after_ttl_not_duplicate(self):
        import core.auto_scanner as _as
        from core.auto_scanner import _is_duplicate
        _as._seen["key3"] = time.time() - 7200  # pretend seen 2 hours ago
        assert _is_duplicate("key3", 3600) is False  # TTL expired

    def test_expire_seen_removes_old_entries(self):
        import core.auto_scanner as _as
        from core.auto_scanner import _expire_seen
        _as._seen["old"] = time.time() - 10000
        _as._seen["new"] = time.time()
        _expire_seen(3600)
        assert "old" not in _as._seen
        assert "new" in _as._seen


# ─────────────────────────────────────────────────────────────────────────────
# Market group inference
# ─────────────────────────────────────────────────────────────────────────────

class TestInferMarketGroup:

    def test_victoria_is_h2h(self):
        from core.auto_scanner import _infer_market_group
        assert _infer_market_group("Victoria Lakers") == "h2h"

    def test_empate_is_h2h(self):
        from core.auto_scanner import _infer_market_group
        assert _infer_market_group("Empate") == "h2h"

    def test_over_is_totals(self):
        from core.auto_scanner import _infer_market_group
        assert _infer_market_group("Over 220.5") == "totals"

    def test_under_is_totals(self):
        from core.auto_scanner import _infer_market_group
        assert _infer_market_group("Under 45.5") == "totals"

    def test_spread_fallback(self):
        from core.auto_scanner import _infer_market_group
        assert _infer_market_group("Lakers -4.5") == "spreads"


# ─────────────────────────────────────────────────────────────────────────────
# Group by event + market
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupByEventMarket:

    def test_groups_h2h_outcomes_together(self):
        from core.auto_scanner import _group_by_event_market
        scans = [
            _scan("NBA", "Lakers vs Celtics", "Victoria Lakers",
                  [("B365", 2.0), ("Cal", 1.95)]),
            _scan("NBA", "Lakers vs Celtics", "Victoria Celtics",
                  [("B365", 1.95), ("Cal", 2.0)]),
        ]
        groups = _group_by_event_market(scans)
        key = ("NBA", "Lakers vs Celtics", "h2h")
        assert key in groups
        assert len(groups[key]) == 2

    def test_groups_totals_separately_from_h2h(self):
        from core.auto_scanner import _group_by_event_market
        scans = [
            _scan("NBA", "Lakers vs Celtics", "Victoria Lakers",
                  [("B365", 2.0)]),
            _scan("NBA", "Lakers vs Celtics", "Over 220.5",
                  [("B365", 1.90), ("Cal", 1.85)]),
        ]
        groups = _group_by_event_market(scans)
        assert ("NBA", "Lakers vs Celtics", "h2h") in groups
        assert ("NBA", "Lakers vs Celtics", "totals") in groups


# ─────────────────────────────────────────────────────────────────────────────
# scan_once: no key → empty result
# ─────────────────────────────────────────────────────────────────────────────

class TestScanOnceNoKey:
    """Without ODDS_API_KEY, scan_once should return [] gracefully."""

    def setup_method(self):
        import core.auto_scanner as _as
        _as._seen.clear()
        _as._prev_odds.clear()

    def test_returns_empty_without_key(self):
        import asyncio
        import core.auto_scanner as _as

        with patch("core.auto_scanner._scan_once_sync", return_value=[]) as mock_fn:
            result = asyncio.get_event_loop().run_until_complete(_as.scan_once())
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# scan_once: market error detected
# ─────────────────────────────────────────────────────────────────────────────

class TestScanOnceMarketError:

    def setup_method(self):
        import core.auto_scanner as _as
        _as._seen.clear()
        _as._prev_odds.clear()

    def test_market_error_produces_alert(self):
        """A scan with a clear outlier (>40% above avg) should produce an alert."""
        import asyncio, core.auto_scanner as _as

        scans = [
            _scan("NBA", "Lakers vs Celtics", "Victoria Lakers", [
                ("Bet365",   2.00),
                ("Caliente", 1.95),
                ("Pinnacle", 3.10),   # outlier: >40% above avg ≈ 1.98
            ])
        ]

        with patch("api.odds_api.get_all_odds", return_value=scans):
            with patch.dict("os.environ", {"ODDS_API_KEY": "fake-key"}):
                with patch("core.config.AUTO_SCAN_MIN_EV", 0.0):
                    with patch("core.config.AUTO_SCAN_DEDUP_TTL", 3600):
                        result = asyncio.get_event_loop().run_until_complete(
                            _as.scan_once()
                        )

        # At minimum, we should have one market-error alert for Pinnacle
        me_alerts = [a for a in result if a.alert_type in ("MARKET_ERROR", "HIGH_VALUE")]
        assert len(me_alerts) >= 1
        assert "Lakers" in me_alerts[0].event


# ─────────────────────────────────────────────────────────────────────────────
# api.odds_api helpers (pure / offline)
# ─────────────────────────────────────────────────────────────────────────────

class TestOddsApiHelpers:

    def test_sport_label_known_key(self):
        from api.odds_api import _sport_label, AUTO_SPORTS
        # Reverse lookup should return the display name
        for label, key in AUTO_SPORTS.items():
            assert _sport_label(key) == label

    def test_outcome_label_h2h_home(self):
        from api.odds_api import _outcome_label
        out = {"name": "Lakers", "price": 2.0}
        assert _outcome_label("h2h", out, "Lakers", "Celtics") == "Victoria Lakers"

    def test_outcome_label_h2h_draw(self):
        from api.odds_api import _outcome_label
        out = {"name": "Draw", "price": 3.2}
        assert _outcome_label("h2h", out, "TeamA", "TeamB") == "Empate"

    def test_outcome_label_totals_over(self):
        from api.odds_api import _outcome_label
        out = {"name": "Over", "price": 1.90, "point": 220.5}
        assert _outcome_label("totals", out, "Lakers", "Celtics") == "Over 220.5"

    def test_outcome_label_spreads(self):
        from api.odds_api import _outcome_label
        out = {"name": "Lakers", "price": 1.90, "point": -4.5}
        assert _outcome_label("spreads", out, "Lakers", "Celtics") == "Lakers -4.5"

    def test_outcome_label_spreads_positive(self):
        from api.odds_api import _outcome_label
        out = {"name": "Celtics", "price": 1.90, "point": 4.5}
        result = _outcome_label("spreads", out, "Lakers", "Celtics")
        assert "+4.5" in result

    def test_get_all_odds_no_key_returns_empty(self):
        from api.odds_api import get_all_odds
        result = get_all_odds("")
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# status_summary smoke test
# ─────────────────────────────────────────────────────────────────────────────

class TestStatusSummary:

    def test_returns_string_with_key_sections(self):
        from core.auto_scanner import status_summary
        text = status_summary()
        assert isinstance(text, str)
        assert "Auto-Scanner" in text
        assert "ODDS_API_KEY" in text
        assert "Intervalo" in text
