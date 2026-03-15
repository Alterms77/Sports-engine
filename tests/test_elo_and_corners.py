"""
Tests for core/corners.py and core/elo.py
"""
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# corners
# ─────────────────────────────────────────────────────────────────────────────

from core.corners import expected_corners, corners_market


class TestExpectedCorners:
    def test_returns_dict_with_required_keys(self):
        result = expected_corners(1.5, 1.2)
        assert "home" in result
        assert "away" in result
        assert "total" in result

    def test_home_plus_away_equals_total(self):
        result = expected_corners(1.5, 1.2)
        assert abs(result["home"] + result["away"] - result["total"]) < 0.01

    def test_higher_xg_team_gets_more_corners(self):
        """Home team with much higher xG should get more corners."""
        result = expected_corners(2.5, 0.5)
        assert result["home"] > result["away"]

    def test_lower_xg_team_gets_fewer_corners(self):
        """Away team with much higher xG should get more corners."""
        result = expected_corners(0.5, 2.5)
        assert result["away"] > result["home"]

    def test_different_matches_give_different_results(self):
        """Model should produce different results for different xG inputs."""
        r1 = expected_corners(1.0, 1.0)
        r2 = expected_corners(2.5, 2.5)
        assert r1["total"] != r2["total"]

    def test_league_specific_base(self):
        """Premier League should give more corners than Liga MX."""
        pl = expected_corners(1.5, 1.2, league="Premier League")
        mx = expected_corners(1.5, 1.2, league="Liga MX")
        assert pl["total"] > mx["total"]

    def test_minimum_corners_floor(self):
        """Both home and away corners should be at least 2.0."""
        result = expected_corners(0.1, 0.1)
        assert result["home"] >= 2.0
        assert result["away"] >= 2.0

    def test_default_league_fallback(self):
        """Unknown league should fall back to default without error."""
        result = expected_corners(1.5, 1.2, league="unknown_league")
        assert result["total"] > 0

    def test_zero_xg_even_split(self):
        """Zero total xG should still return valid corners."""
        result = expected_corners(0.0, 0.0)
        assert result["home"] >= 2.0
        assert result["away"] >= 2.0


class TestCornersMarket:
    def test_returns_required_keys(self):
        data = expected_corners(1.5, 1.2)
        market = corners_market(data)
        for key in ("home", "away", "total", "line", "over_prob", "under_prob", "suggestion"):
            assert key in market

    def test_over_suggestion_when_total_above_line(self):
        data = {"home": 6.0, "away": 5.0, "total": 11.0}
        market = corners_market(data, line=9.5)
        assert market["suggestion"] == "Over"

    def test_under_suggestion_when_total_below_line(self):
        data = {"home": 4.0, "away": 4.0, "total": 8.0}
        market = corners_market(data, line=9.5)
        assert market["suggestion"] == "Under"

    def test_probabilities_sum_to_100(self):
        data = expected_corners(1.5, 1.2)
        market = corners_market(data)
        assert abs(market["over_prob"] + market["under_prob"] - 100.0) < 0.1

    def test_probabilities_are_valid_percentages(self):
        data = expected_corners(1.5, 1.2)
        market = corners_market(data)
        assert 0 <= market["over_prob"] <= 100
        assert 0 <= market["under_prob"] <= 100


# ─────────────────────────────────────────────────────────────────────────────
# elo
# ─────────────────────────────────────────────────────────────────────────────

from core.elo import (
    expected_score,
    update_elo,
    elo_xg_adjustment,
    load_elo_ratings,
    save_elo_ratings,
    DEFAULT_ELO,
)


class TestExpectedScore:
    def test_equal_ratings_give_50_percent(self):
        assert abs(expected_score(1500, 1500) - 0.5) < 1e-9

    def test_higher_rating_gives_higher_expected_score(self):
        assert expected_score(1600, 1500) > 0.5

    def test_lower_rating_gives_lower_expected_score(self):
        assert expected_score(1400, 1500) < 0.5

    def test_symmetry(self):
        a = expected_score(1600, 1400)
        b = expected_score(1400, 1600)
        assert abs(a + b - 1.0) < 1e-9

    def test_output_range(self):
        for diff in (-400, -200, 0, 200, 400):
            score = expected_score(1500 + diff, 1500)
            assert 0.0 < score < 1.0


class TestUpdateElo:
    def test_winner_gains_loser_loses(self):
        ratings = {}
        update_elo(ratings, "TeamA", "TeamB", 2, 0)
        assert ratings["TeamA"] > DEFAULT_ELO
        assert ratings["TeamB"] < DEFAULT_ELO

    def test_draw_moves_toward_equal(self):
        """Equal teams drawing should stay near 1500."""
        ratings = {}
        update_elo(ratings, "TeamA", "TeamB", 1, 1)
        assert abs(ratings["TeamA"] - DEFAULT_ELO) < 1.0
        assert abs(ratings["TeamB"] - DEFAULT_ELO) < 1.0

    def test_total_elo_is_conserved(self):
        """Sum of Elo ratings should be approximately conserved."""
        ratings = {"TeamA": 1600.0, "TeamB": 1400.0}
        total_before = ratings["TeamA"] + ratings["TeamB"]
        update_elo(ratings, "TeamA", "TeamB", 3, 1)
        total_after = ratings["TeamA"] + ratings["TeamB"]
        assert abs(total_before - total_after) < 1e-6

    def test_upset_gives_larger_gain(self):
        """Underdog winning should gain more points than favourite winning."""
        # Underdog (1300) beats favourite (1700)
        r_upset = {"Underdog": 1300.0, "Favourite": 1700.0}
        update_elo(r_upset, "Underdog", "Favourite", 1, 0)
        underdog_gain = r_upset["Underdog"] - 1300.0

        # Favourite (1700) beats underdog (1300)
        r_expected = {"Favourite": 1700.0, "Underdog": 1300.0}
        update_elo(r_expected, "Favourite", "Underdog", 1, 0)
        favourite_gain = r_expected["Favourite"] - 1700.0

        assert underdog_gain > favourite_gain

    def test_new_teams_start_at_default(self):
        ratings = {}
        update_elo(ratings, "New1", "New2", 1, 0)
        assert "New1" in ratings
        assert "New2" in ratings


class TestEloXgAdjustment:
    def test_equal_ratings_no_adjustment(self):
        home_mult, away_mult = elo_xg_adjustment(1500, 1500)
        assert abs(home_mult - 1.0) < 1e-9
        assert abs(away_mult - 1.0) < 1e-9

    def test_home_advantage_increases_home_xg(self):
        home_mult, away_mult = elo_xg_adjustment(1700, 1500)
        assert home_mult > 1.0
        assert away_mult < 1.0

    def test_home_disadvantage_decreases_home_xg(self):
        home_mult, away_mult = elo_xg_adjustment(1300, 1500)
        assert home_mult < 1.0
        assert away_mult > 1.0

    def test_max_adjustment_capped_at_15_percent(self):
        home_mult, away_mult = elo_xg_adjustment(3000, 0)
        assert home_mult <= 1.15
        assert away_mult >= 0.85

    def test_symmetry(self):
        h, a = elo_xg_adjustment(1600, 1400)
        h2, a2 = elo_xg_adjustment(1400, 1600)
        assert abs(h - a2) < 1e-6
        assert abs(a - h2) < 1e-6


class TestLoadSaveElo:
    def test_load_returns_dict(self, tmp_path):
        """Loading from a non-existent file returns an empty dict."""
        result = load_elo_ratings()
        assert isinstance(result, dict)

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        """Data saved can be loaded back correctly."""
        import core.elo as elo_module
        test_file = str(tmp_path / "elo_test.json")
        monkeypatch.setattr(elo_module, "ELO_FILE", test_file)

        ratings = {"TeamA": 1600.0, "TeamB": 1400.0}
        save_elo_ratings(ratings)
        loaded = load_elo_ratings()
        assert loaded["TeamA"] == pytest.approx(1600.0)
        assert loaded["TeamB"] == pytest.approx(1400.0)
