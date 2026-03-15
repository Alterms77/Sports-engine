"""
Tests for the MLB team name resolver (sports.baseball.resolve_team).

Covers canonical names, abbreviations, and common variants that users
are known to type in production (e.g. "Chicago White Sox", "White Sox",
"CWS", etc.).
"""
import pytest
from sports.baseball import resolve_team, suggest_teams


# ── resolve_team ─────────────────────────────────────────────────────────────

class TestResolveTeam:
    """resolve_team should map any recognised alias to a canonical name."""

    # Chicago White Sox variants
    @pytest.mark.parametrize("alias", [
        "Chicago White Sox",
        "chicago white sox",
        "White Sox",
        "white sox",
        "CWS",
        "cws",
        "Chi White Sox",
        "chi white sox",
        "Chicago Sox",
        "chicago sox",
        "Chi Sox",
        "chi sox",
    ])
    def test_chicago_white_sox_aliases(self, alias):
        assert resolve_team(alias) == "Chicago White Sox", (
            f"Expected 'Chicago White Sox' but got {resolve_team(alias)!r} for input {alias!r}"
        )

    # Other common teams to make sure they still work
    @pytest.mark.parametrize("alias,expected", [
        ("Yankees", "New York Yankees"),
        ("yankees", "New York Yankees"),
        ("NY Yankees", "New York Yankees"),
        ("Red Sox", "Boston Red Sox"),
        ("boston", "Boston Red Sox"),
        ("Dodgers", "Los Angeles Dodgers"),
        ("la dodgers", "Los Angeles Dodgers"),
        ("Cubs", "Chicago Cubs"),
        ("chicago cubs", "Chicago Cubs"),
        ("Braves", "Atlanta Braves"),
        ("astros", "Houston Astros"),
        ("mets", "New York Mets"),
        ("guardians", "Cleveland Guardians"),
        ("indians", "Cleveland Guardians"),
    ])
    def test_other_teams(self, alias, expected):
        assert resolve_team(alias) == expected

    def test_unknown_team_returns_none(self):
        assert resolve_team("Nonexistent Team FC") is None

    def test_empty_string_returns_none(self):
        assert resolve_team("") is None

    def test_leading_trailing_whitespace(self):
        assert resolve_team("  White Sox  ") == "Chicago White Sox"


# ── suggest_teams ─────────────────────────────────────────────────────────────

class TestSuggestTeams:
    """suggest_teams should include Chicago White Sox for relevant queries."""

    def test_white_sox_suggestion(self):
        suggestions = suggest_teams("white sox")
        assert "Chicago White Sox" in suggestions

    def test_cws_suggestion(self):
        suggestions = suggest_teams("cws")
        assert "Chicago White Sox" in suggestions

    def test_unknown_returns_empty(self):
        assert suggest_teams("zzzzunknownteam") == []
