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
- format_result_confirmation: Telegram output
- format_history_summary: Telegram output with calibration section
"""

import json
import os
import pytest
import tempfile

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
    Redirect all history I/O to a temporary directory so tests are isolated
    and don't touch the real data/parlay_history.json.
    """
    import core.parlay_history as ph
    hist_file = str(tmp_path / "parlay_history.json")
    monkeypatch.setattr(ph, "_HIST_FILE", hist_file)
    monkeypatch.setattr(ph, "_DATA_DIR",  str(tmp_path))
    # Reset the module-level lock state (each test gets a clean lock)
    monkeypatch.setattr(ph, "_LOCK", __import__("threading").Lock())
    yield ph   # return the module so tests can call its functions directly


def _leg(match="A vs B", pick="Over 2.5", market_type="totals",
         sport_emoji="⚽", prob=82.0):
    return {
        "match":            match,
        "pick":             pick,
        "market_type":      market_type,
        "sport_emoji":      sport_emoji,
        "prob":             prob,
        "raw_prob":         prob,
        "confidence":       "ALTA",
        "league":           "Test",
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

    def test_file_created(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        assert os.path.exists(tmp_history._HIST_FILE)

    def test_file_contains_valid_json(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert isinstance(data, list) and len(data) == 1

    def test_record_has_correct_tier(self, tmp_history):
        tmp_history.save_parlay([_leg()], "balanced", 72.0)
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert data[0]["tier"] == "balanced"

    def test_record_has_correct_combined_prob(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 72.5)
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert data[0]["combined_prob"] == 72.5

    def test_legs_stored_correctly(self, tmp_history):
        legs = [_leg("A vs B", "Over 2.5"), _leg("C vs D", "Moneyline")]
        tmp_history.save_parlay(legs, "balanced", 60.0)
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert len(data[0]["legs"]) == 2
        assert data[0]["legs"][0]["match"] == "A vs B"

    def test_legs_result_is_null_initially(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert all(l["result"] is None for l in data[0]["legs"])

    def test_multiple_saves_accumulate(self, tmp_history):
        tmp_history.save_parlay([_leg()], "safe", 82.0)
        tmp_history.save_parlay([_leg()], "safe", 75.0)
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert len(data) == 2


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

    def test_results_persisted_to_file(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        tmp_history.record_results(pid, ["L"])
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert data[0]["legs"][0]["result"] == "L"

    def test_case_insensitive_id(self, tmp_history):
        pid = tmp_history.save_parlay([_leg()], "safe", 82.0)
        res = tmp_history.record_results(pid.lower(), ["W"])
        assert res["found"] is True

    def test_results_shorter_than_legs_leaves_remainder_null(self, tmp_history):
        pid = tmp_history.save_parlay([_leg(), _leg("C vs D")], "safe", 72.0)
        tmp_history.record_results(pid, ["W"])  # only 1 result for 2 legs
        with open(tmp_history._HIST_FILE) as f:
            data = json.load(f)
        assert data[0]["legs"][0]["result"] == "W"
        assert data[0]["legs"][1]["result"] is None


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
        # 3 wins out of 4 legs → 75 %
        for result in ["W", "W", "W", "L"]:
            pid = tmp_history.save_parlay([_leg(prob=80.0)], "safe", 80.0)
            tmp_history.record_results(pid, [result])
        stats = tmp_history.get_calibration_stats()
        assert abs(stats["overall"]["hit_rate"] - 75.0) < 1.0

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
