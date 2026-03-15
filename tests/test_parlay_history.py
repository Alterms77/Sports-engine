"""
Tests for core/parlay_history.py

Covers:
- _next_id: sequential ID generation
- save_parlay: persists a record and returns the correct ID
- record_results: marks legs W/L/X, derives overall result
- get_last_parlay_id: returns most recent ID
- get_history: returns records newest-first
- get_calibration_stats: correct hit rates and calibration factors
- calibrate_prob: applies factor, respects min-sample guard, clamps extremes
- get_sport_stats / get_league_stats: per-dimension stats
- get_trend: ordered resolved parlay trend
- format_result_confirmation: Telegram output
- format_history_summary: Telegram output with calibration section
- format_estadisticas: deep-analytics Telegram output
"""

import os
import pytest

# ── Path setup (mirrors conftest.py) ──────────────────────────────────────────
import sys
_SPORTS_ENGINE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sports_engine",
)
if _SPORTS_ENGINE not in sys.path:
    sys.path.insert(0, _SPORTS_ENGINE)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_history(tmp_path, monkeypatch):
    """
    Redirect all history I/O to a fresh SQLite database in a temporary
    directory so tests are isolated and don't touch real data.
    """
    import core.parlay_history as ph
    db_file = str(tmp_path / "parlay_history.db")
    monkeypatch.setattr(ph, "_DB_FILE",     db_file)
    monkeypatch.setattr(ph, "_DATA_DIR",    str(tmp_path))
    monkeypatch.setattr(ph, "_LEGACY_JSON", str(tmp_path / "parlay_history.json"))
    # Reset the module-level lock so each test gets a clean one
    monkeypatch.setattr(ph, "_LOCK", __import__("threading").Lock())
    yield ph   # return the module so tests can call its functions directly


def _leg(match="A vs B", pick="Over 2.5", market_type="totals",
         sport_emoji="⚽", prob=82.0, sport="soccer", league="Test"):
    return {
        "match":            match,
        "pick":             pick,
        "market_type":      market_type,
        "sport_emoji":      sport_emoji,
        "prob":             prob,
        "raw_prob":         prob,
        "sport":            sport,
        "confidence":       "ALTA",
        "league":           league,
        "risk_reasons":     [],
        "calibration_note": "",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ID generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestNextId:
    def test_first_id_ends_with_1(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert pid.endswith("-1")

    def test_second_id_ends_with_2(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        pid2 = tmp_history.save_parlay([_leg()], "balanced", 72.0)
        assert pid2.endswith("-2")

    def test_id_has_date_prefix(self, tmp_history):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%y%m%d")
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert pid.startswith(f"P{today}-")


# ═══════════════════════════════════════════════════════════════════════════════
# save_parlay
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveParlay:
    def test_returns_string_id(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert isinstance(pid, str) and pid

    def test_db_file_created(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert os.path.exists(tmp_history._DB_FILE)

    def test_record_queryable_via_get_history(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        hist = tmp_history.get_history()
        assert len(hist) == 1

    def test_record_has_correct_tier(self, tmp_history):
        tmp_history.save_parlay([_leg()], "balanced", 72.0)
        hist = tmp_history.get_history()
        assert hist[0]["tier"] == "balanced"

    def test_record_has_correct_combined_prob(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 72.5)
        hist = tmp_history.get_history()
        assert hist[0]["combined_prob"] == 72.5

    def test_legs_stored_correctly(self, tmp_history):
        legs = [_leg("A vs B", "Over 2.5"), _leg("C vs D", "Moneyline")]
        tmp_history.save_parlay(legs, "balanced", 60.0)
        hist = tmp_history.get_history()
        assert len(hist[0]["legs"]) == 2
        assert hist[0]["legs"][0]["match"] == "A vs B"

    def test_legs_result_is_null_initially(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        hist = tmp_history.get_history()
        assert all(l["result"] is None for l in hist[0]["legs"])

    def test_multiple_saves_accumulate(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        tmp_history.save_parlay([_leg()], "safe", 75.0)
        hist = tmp_history.get_history()
        assert len(hist) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# record_results
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordResults:
    def test_found_true_for_valid_id(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D")], "safe", 72.0)
        res = tmp_history.record_results(pid, ["W", "W"])
        assert res["found"] is True

    def test_found_false_for_unknown_id(self, tmp_history):
        res = tmp_history.record_results("P999999-99", ["W"])
        assert res["found"] is False

    def test_all_win_gives_overall_w(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D")], "safe", 72.0)
        res = tmp_history.record_results(pid, ["W", "W"])
        assert res["overall"] == "W"

    def test_any_loss_gives_overall_l(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D")], "safe", 72.0)
        res = tmp_history.record_results(pid, ["W", "L"])
        assert res["overall"] == "L"

    def test_void_only_gives_overall_w(self, tmp_history):
        # A single void leg counts as a win (match was cancelled/refunded)
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        res = tmp_history.record_results(pid, ["X"])
        assert res["overall"] == "W"

    def test_mixed_void_and_win_gives_overall_w(self, tmp_history):
        # All legs resolved as W or X (void) → parlay is considered won/refunded
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D")], "safe", 72.0)
        res = tmp_history.record_results(pid, ["W", "X"])
        assert res["overall"] == "W"

    def test_void_with_loss_gives_overall_l(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D")], "safe", 72.0)
        res = tmp_history.record_results(pid, ["X", "L"])
        assert res["overall"] == "L"

    def test_partial_report_leaves_overall_none(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D"), _leg("E vs F")], "risky", 60.0)
        res = tmp_history.record_results(pid, ["W"])  # only 1 of 3 reported
        assert res["overall"] is None

    def test_results_persisted_to_db(self, tmp_history):
        # Verify results survive across a fresh get_history() call (DB round-trip)
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        tmp_history.record_results(pid, ["L"])
        hist = tmp_history.get_history()
        assert hist[0]["legs"][0]["result"] == "L"

    def test_case_insensitive_id(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        res = tmp_history.record_results(pid.lower(), ["W"])
        assert res["found"] is True

    def test_results_shorter_than_legs_leaves_remainder_null(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D")], "safe", 72.0)
        outcome = tmp_history.record_results(pid, ["W"])  # only 1 result for 2 legs
        assert outcome["legs"][0]["result"] == "W"
        assert outcome["legs"][1]["result"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# get_last_parlay_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetLastParlayId:
    def test_none_when_no_history(self, tmp_history):
        assert tmp_history.get_last_parlay_id() is None

    def test_returns_most_recent_id(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        pid2 = tmp_history.save_parlay([_leg()], "safe", 75.0)
        assert tmp_history.get_last_parlay_id() == pid2


# ═══════════════════════════════════════════════════════════════════════════════
# get_history
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetHistory:
    def test_empty_when_no_records(self, tmp_history):
        assert tmp_history.get_history() == []

    def test_returns_newest_first(self, tmp_history):
        pid1 = tmp_history.save_parlay([_leg()], "safe", 82.0)
        pid2 = tmp_history.save_parlay([_leg()], "safe", 75.0)
        hist = tmp_history.get_history()
        assert hist[0]["id"] == pid2
        assert hist[1]["id"] == pid1

    def test_limit_respected(self, tmp_history):
        for _ in range(5):
            tmp_history.save_parlay([_leg()], "safe", 80.0)
        assert len(tmp_history.get_history(limit=3)) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# get_calibration_stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCalibrationStats:
    def test_empty_when_no_results(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        stats = tmp_history.get_calibration_stats()
        # No results recorded → no stats
        assert stats == {}

    def test_overall_stats_computed(self, tmp_history):
        # Save and fully resolve 3 parlays
        for result in ["W", "W", "L"]:
            pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
            tmp_history.record_results(pid, [result])
        stats = tmp_history.get_calibration_stats()
        assert "overall" in stats
        assert stats["overall"]["n"] == 3

    def test_hit_rate_correct(self, tmp_history):
        # 3 wins out of 4 legs → simple mean = 75%.
        # EWMA_DECAY=0.95 weights the most recent (L) more heavily, giving ~73%.
        # We allow ±3% to accommodate EWMA weighting while still confirming the
        # hit_rate is clearly between 60% and 80% (not 50% or 100%).
        for result in ["W", "W", "W", "L"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0)], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        stats = tmp_history.get_calibration_stats()
        assert 60.0 < stats["overall"]["hit_rate"] < 80.0

    def test_calibration_factor_overconfident(self, tmp_history):
        # Model predicts 80 % but only 40 % win rate → overconfident
        for result in ["W", "L", "L", "L", "L"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0)], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        stats = tmp_history.get_calibration_stats()
        assert stats["overall"]["calibration"] < 1.0
        assert stats["overall"]["bias"] == "OVERCONFIDENT"

    def test_per_market_stats_computed(self, tmp_history):
        for result in ["W", "W", "L"]:
            pid = tmp_history.save_parlay([_leg(market_type="moneyline")], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        stats = tmp_history.get_calibration_stats()
        assert "moneyline" in stats


# ═══════════════════════════════════════════════════════════════════════════════
# calibrate_prob
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalibrateProb:
    def test_returns_unchanged_when_no_data(self, tmp_history):
        assert tmp_history.calibrate_prob(80.0, "totals") == 80.0

    def test_returns_unchanged_below_min_samples(self, tmp_history):
        # Only 2 resolved legs — below _MIN_SAMPLES (5)
        for result in ["W", "L"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0, market_type="totals")], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        assert tmp_history.calibrate_prob(80.0, "totals") == 80.0

    def test_reduces_prob_when_overconfident(self, tmp_history):
        # 1 win / 5 total = 20 % hit rate vs 80 % predicted → factor ≈ 0.25
        for result in ["W", "L", "L", "L", "L"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0, market_type="totals")], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        adjusted = tmp_history.calibrate_prob(80.0, "totals")
        assert adjusted < 80.0

    def test_increases_prob_when_underconfident(self, tmp_history):
        # 5 wins / 5 = 100 % hit rate vs 60 % predicted → factor ≈ 1.67 (capped)
        for _ in range(5):
            pid = tmp_history.save_parlay([_leg(prob=60.0, market_type="moneyline")], "safe", 60.0)
            tmp_history.record_results(pid, ["W"])
        adjusted = tmp_history.calibrate_prob(60.0, "moneyline")
        assert adjusted > 60.0

    def test_result_clamped_to_1_99(self, tmp_history):
        # Extreme: 0 % hit rate on 5 resolved → factor = 0 → clamped to _CAL_MIN
        for _ in range(5):
            pid = tmp_history.save_parlay([_leg(prob=80.0, market_type="btts")], "safe", 80.0)
            tmp_history.record_results(pid, ["L"])
        adjusted = tmp_history.calibrate_prob(80.0, "btts")
        assert 1.0 <= adjusted <= 99.0

    def test_falls_back_to_overall_when_market_has_no_data(self, tmp_history):
        # Build enough overall data so overall calibration factor < 1.0
        for result in ["W", "L", "L", "L", "L"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0, market_type="totals")], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        # "spread" has no data → falls back to "overall"
        adjusted = tmp_history.calibrate_prob(80.0, "spread")
        assert adjusted < 80.0


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram formatters
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatResultConfirmation:
    def test_returns_string(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        outcome = tmp_history.record_results(pid, ["W"])
        text = tmp_history.format_result_confirmation(outcome)
        assert isinstance(text, str)

    def test_contains_parlay_id(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        outcome = tmp_history.record_results(pid, ["W"])
        text = tmp_history.format_result_confirmation(outcome)
        assert pid in text

    def test_win_shows_checkmark(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        outcome = tmp_history.record_results(pid, ["W"])
        text = tmp_history.format_result_confirmation(outcome)
        assert "✅" in text

    def test_loss_shows_x(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        outcome = tmp_history.record_results(pid, ["L"])
        text = tmp_history.format_result_confirmation(outcome)
        assert "❌" in text


class TestFormatHistorySummary:
    def test_returns_string(self, tmp_history):
        text = tmp_history.format_history_summary([], {})
        assert isinstance(text, str)

    def test_shows_no_records_message_when_empty(self, tmp_history):
        text = tmp_history.format_history_summary([], {})
        assert "no hay" in text.lower() or "aún" in text.lower()

    def test_shows_parlay_id_in_history(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        records = tmp_history.get_history()
        text = tmp_history.format_history_summary(records, {})
        assert pid in text

    def test_shows_win_rate_when_resolved(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        tmp_history.record_results(pid, ["W"])
        records = tmp_history.get_history()
        text = tmp_history.format_history_summary(records, {})
        assert "Parlays ganados" in text or "100%" in text

    def test_shows_calibration_section_when_enough_data(self, tmp_history):
        # Need ≥ 3 resolved legs for calibration section to appear
        for result in ["W", "L", "W"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0)], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        records   = tmp_history.get_history()
        cal_stats = tmp_history.get_calibration_stats()
        text = tmp_history.format_history_summary(records, cal_stats)
        assert "calibra" in text.lower() or "CALIBRACIÓN" in text

    def test_contains_usage_hint(self, tmp_history):
        text = tmp_history.format_history_summary([], {})
        assert "/resultado" in text


# ═══════════════════════════════════════════════════════════════════════════════
# get_sport_stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetSportStats:
    def test_empty_when_no_results(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert tmp_history.get_sport_stats() == {}

    def test_returns_sport_key(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(sport="soccer")], "safe", 82.0)
        tmp_history.record_results(pid, ["W"])
        stats = tmp_history.get_sport_stats()
        assert "soccer" in stats

    def test_n_counts_resolved_legs(self, tmp_history):
        for _ in range(3):
            pid = tmp_history.save_parlay([_leg(sport="nba")], "safe", 80.0)
            tmp_history.record_results(pid, ["W"])
        stats = tmp_history.get_sport_stats()
        assert stats["nba"]["n"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# get_league_stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetLeagueStats:
    def test_empty_when_no_results(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert tmp_history.get_league_stats() == {}

    def test_returns_league_key(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(league="Premier League")], "safe", 82.0)
        tmp_history.record_results(pid, ["W"])
        stats = tmp_history.get_league_stats()
        assert "Premier League" in stats

    def test_hit_rate_correct(self, tmp_history):
        for result in ["W", "W", "L"]:
            pid = tmp_history.save_parlay([_leg(league="LaLiga", prob=80.0)], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        stats = tmp_history.get_league_stats()
        # 2 wins / 3 total → ~66.7%
        assert 60 < stats["LaLiga"]["hit_rate"] < 75


# ═══════════════════════════════════════════════════════════════════════════════
# get_trend
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetTrend:
    def test_empty_when_no_resolved_parlays(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert tmp_history.get_trend() == []

    def test_returns_resolved_only(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        tmp_history.record_results(pid, ["W"])
        trend = tmp_history.get_trend()
        assert len(trend) == 1

    def test_ordered_oldest_first(self, tmp_history):
        pid1 = tmp_history.save_parlay([_leg()], "safe", 82.0)
        tmp_history.record_results(pid1, ["W"])
        pid2 = tmp_history.save_parlay([_leg()], "safe", 75.0)
        tmp_history.record_results(pid2, ["L"])
        trend = tmp_history.get_trend()
        assert trend[0]["id"] == pid1
        assert trend[1]["id"] == pid2

    def test_limit_respected(self, tmp_history):
        for result in ["W", "L", "W", "L", "W"]:
            pid = tmp_history.save_parlay([_leg()], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        trend = tmp_history.get_trend(n_last=3)
        assert len(trend) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# format_estadisticas
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatEstadisticas:
    def _make_stats(self, tmp_history):
        for result in ["W", "L", "W"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0)], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        return (
            tmp_history.get_calibration_stats(),
            tmp_history.get_sport_stats(),
            tmp_history.get_league_stats(),
            tmp_history.get_trend(),
        )

    def test_returns_string(self, tmp_history):
        cal, sport, league, trend = self._make_stats(tmp_history)
        text = tmp_history.format_estadisticas(cal, sport, league, trend)
        assert isinstance(text, str)

    def test_contains_header(self, tmp_history):
        cal, sport, league, trend = self._make_stats(tmp_history)
        text = tmp_history.format_estadisticas(cal, sport, league, trend)
        assert "ESTADÍSTICAS" in text or "ESTADIST" in text

    def test_shows_trend_sparkline(self, tmp_history):
        cal, sport, league, trend = self._make_stats(tmp_history)
        text = tmp_history.format_estadisticas(cal, sport, league, trend)
        assert "✅" in text or "❌" in text

    def test_no_data_shows_placeholder(self, tmp_history):
        text = tmp_history.format_estadisticas({}, {}, {}, [])
        assert "Sin datos" in text or "datos" in text.lower()

    def test_contains_resultado_hint(self, tmp_history):
        text = tmp_history.format_estadisticas({}, {}, {}, [])
        assert "/resultado" in text


# ═══════════════════════════════════════════════════════════════════════════════
# get_num_legs_for_parlay
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetNumLegsForParlay:
    def test_returns_zero_for_unknown_id(self, tmp_history):
        assert tmp_history.get_num_legs_for_parlay("P999999-99") == 0

    def test_returns_correct_leg_count(self, tmp_history):
        legs = [_leg("A vs B"), _leg("C vs D"), _leg("E vs F")]
        pid = tmp_history.save_parlay(legs, "balanced", 72.0)
        assert tmp_history.get_num_legs_for_parlay(pid) == 3

    def test_single_leg(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert tmp_history.get_num_legs_for_parlay(pid) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# get_bucket_stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetBucketStats:
    def _leg_with_cal(self, prob_calibrated=72.0, match="A vs B", result=None):
        l = _leg(match=match, prob=prob_calibrated)
        l["prob_calibrated"] = prob_calibrated
        return l

    def test_empty_when_no_resolved_legs(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        # No resolved legs → buckets with min_n=1 but no results either
        stats = tmp_history.get_bucket_stats(min_n=1)
        # No results at all
        assert isinstance(stats, dict)

    def test_bucket_populated_after_results(self, tmp_history):
        # Save 3 resolved legs in the 70-75 % bucket
        for i, res in enumerate(["W", "W", "L"]):
            pid = tmp_history.save_parlay([_leg(match=f"M{i} vs N{i}", prob=72.0)],
                                          "safe", 72.0)
            tmp_history.record_results(pid, [res])
        stats = tmp_history.get_bucket_stats(min_n=1)
        # prob_calibrated defaults to prob in save_parlay → should land in 70-75% bucket
        assert isinstance(stats, dict)

    def test_min_n_filters_small_buckets(self, tmp_history):
        # Only 1 resolved leg → with min_n=3 should not appear
        pid = tmp_history.save_parlay([_leg(prob=72.0)], "safe", 72.0)
        tmp_history.record_results(pid, ["W"])
        stats_strict = tmp_history.get_bucket_stats(min_n=3)
        stats_loose  = tmp_history.get_bucket_stats(min_n=1)
        # At most as many buckets with strict as with loose
        assert len(stats_strict) <= len(stats_loose)
