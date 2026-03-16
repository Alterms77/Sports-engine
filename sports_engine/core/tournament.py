"""
Tournament stage detection and prediction adjustments for Sports Engine.

Detects whether a match is in a knockout stage (octavos de final, cuartos,
semifinal, final), a qualification/classification round, or a group stage,
from the API-Football ``round`` string or an ESPN game name, then supplies
sport-calibrated prediction modifiers.

Stage categories
----------------
Knockout stages (is_knockout_stage → True)
  final, super_bowl, championship_final, semi_final, quarter_final,
  round_of_16, round_of_32, playoff_wild, playoffs, liguilla, second_leg

Qualification / classification (is_qualification → True)
  qualification, nations_league_final_four, group_stage, nations_league

Regular / no adjustment
  regular, repechaje

Public API
----------
detect_stage(round_str)          → (stage_key, display_label_es)
get_stage_modifiers(stage_key)   → (xg_multiplier, home_adv_multiplier, btts_adj_pct)
is_knockout_stage(stage_key)     → bool
is_qualification(stage_key)      → bool
stage_importance(stage_key)      → int
IMPORTANT_TOURNAMENT_IDS         → dict[int, str]   API-Football league IDs
QUALIFICATION_TOURNAMENT_IDS     → dict[int, str]   Qualification competition IDs
"""

from __future__ import annotations

# ── Additional important tournament IDs (API-Football) ───────────────────────
# These supplement ALLOWED_LEAGUE_IDS in config.py so that today_matches.csv
# includes high-profile cup and international competition fixtures.
IMPORTANT_TOURNAMENT_IDS: dict[int, str] = {
    3:    "UEFA Europa League",
    848:  "UEFA Europa Conference League",
    1:    "FIFA World Cup",
    143:  "Copa del Rey",
    137:  "Coppa Italia",
    65:   "Coupe de France",
    45:   "FA Cup",
    48:   "EFL Cup (Carabao Cup)",
    81:   "DFB-Pokal",
    769:  "Leagues Cup",
    9:    "Copa América",
    25:   "Gold Cup",
    26:   "CONCACAF Nations League",
    531:  "UEFA Super Cup",
    34:   "Copa Libertadores",
    13:   "Copa Sudamericana",
    436:  "Club World Cup",
    667:  "FIFA Club World Cup",
    17:   "Copa MX",
    10:   "FIFA World Cup Qualification",
}

# ── Qualification / classification competition IDs (API-Football) ─────────────
# These are added separately so the bot can fetch classification-phase fixtures.
QUALIFICATION_TOURNAMENT_IDS: dict[int, str] = {
    32:   "World Cup Qualification - Europe (UEFA)",
    29:   "World Cup Qualification - CONMEBOL",
    31:   "World Cup Qualification - CONCACAF",
    30:   "World Cup Qualification - CAF",
    33:   "World Cup Qualification - AFC",
    41:   "World Cup Qualification - OFC",
    4:    "UEFA Euro Championship",
    960:  "UEFA Euro Qualification",
    5:    "UEFA Nations League",
    8:    "UEFA Champions League Qualifying",
    847:  "UEFA Conference League Qualifying",
    176:  "Copa América Qualification",
    1069: "CONMEBOL Pre-Olimpico",
    890:  "CONCACAF W Championship",
    622:  "CAF Champions League",
    20:   "AFC Champions League",
    672:  "OFC Champions League",
}

# ── Stage keyword patterns → (stage_key, display_label_es) ───────────────────
# Ordered from most specific to least specific; the first matching pattern wins.
# Normalise to lower-case before matching.
#
# IMPORTANT ordering rules:
#   1. More-specific stage names (e.g. "conference finals") MUST come before
#      the generic "final" check so they are not swallowed by it.
#   2. The generic "final" keyword is intentionally last among the knockout
#      patterns; it uses ("final",) which is a sub-string of many strings, so
#      all stricter patterns must already have been exhausted.
_STAGE_PATTERNS: list[tuple[tuple[str, ...], str, str]] = [
    # ── Super Bowl / Grand Finals (sport-specific "the final") ──
    (("super bowl",),
     "super_bowl",         "🏈🏆 Super Bowl"),
    (("nba finals",),
     "championship_final", "🏀🏆 Final NBA"),
    (("world series",),
     "championship_final", "⚾🏆 World Series"),
    (("championship final", "grand final"),
     "championship_final", "🏆 Gran Final"),

    # ── Nations League Final Four (before generic "final") ──
    (("final four", "nations league final",),
     "nations_league_final_four", "🌍🏆 Final Four Nations League"),

    # ── Semi-finals — MUST come before generic "final" ──────────────────────
    # "conference finals" contains the substring " final", so it must be
    # caught here before the generic ("final",) pattern below.
    (("semi-final", "semifinal", "semi final",
      "conference finals", "conference championship",),
     "semi_final",         "🔥 Semifinal"),

    # ── Quarter-finals — before generic "final" ──────────────────────────────
    (("quarter-final", "quarterfinal", "quarter final",
      "divisional", "cuartos",),
     "quarter_final",      "⚡ Cuartos de Final"),

    # ── Round of 16 — before generic "final" ─────────────────────────────────
    (("round of 16", "last 16", "1/8", "octavos",),
     "round_of_16",        "🎯 Octavos de Final"),

    # ── Round of 32 / last 32 ────────────────────────────────────────────────
    (("round of 32", "last 32", "1/16", "dieciseisavos",),
     "round_of_32",        "🎯 Dieciseisavos de Final"),

    # ── Play-off / wild-card (before generic "playoff") ──────────────────────
    (("wild card", "wildcard", "wild-card",),
     "playoff_wild",       "🎯 Ronda Comodín"),

    # ── Qualifying play-offs (CL/EL entry gate) — before generic playoffs ────
    (("qualifying play-off", "qualifying play off",
      "qualifying playoff",),
     "qual_playoff",       "🔓 Play-off de Clasificación"),

    # ── Generic playoffs ─────────────────────────────────────────────────────
    (("playoff", "play-off",),
     "playoffs",           "🏟️ Playoffs"),

    # ── GENERIC FINAL — uses bare "final" keyword; must come AFTER all
    #    patterns that match strings which also contain "final" (semi-final,
    #    quarter-final, etc.) ───────────────────────────────────────────────
    (("final",),
     "final",              "🏆 Final"),

    # ── Liga MX Liguilla (Apertura/Clausura knockout — generic round) ────────
    # Specific sub-rounds ("Liguilla - Semifinal", "Liguilla - Cuartos") are
    # already caught above; this handles the bare "Liguilla" label.
    (("liguilla",),
     "liguilla",           "🇲🇽 Liguilla"),

    # ── Repechaje (Liga MX classification play-in) ────────────────────────────
    (("repechaje",),
     "repechaje",          "🔁 Repechaje"),

    # ── 2nd leg (same stage, add visual cue but no extra modifier) ───────────
    (("2nd leg", "second leg", "vuelta", "2da vuelta",),
     "second_leg",         "🔁 Partido de Vuelta"),

    # ─────────────────────────────────────────────────────────────────────────
    # QUALIFICATION / CLASSIFICATION stages
    # These must come BEFORE the generic "group stage" patterns so that, e.g.,
    # "Qualifying Round - Group A" resolves to "qualification" not "group_stage".
    # ─────────────────────────────────────────────────────────────────────────

    # ── Numbered qualifying rounds ────────────────────────────────────────────
    (("1st qualifying", "first qualifying",
      "2nd qualifying", "second qualifying",
      "3rd qualifying", "third qualifying",
      "4th qualifying", "fourth qualifying",
      "preliminary round", "pre-qualification",
      "qualifying round", "qualification round",),
     "qualification",      "📋 Ronda Clasificatoria"),

    # ── Generic "qualification" / "clasificatoria" keyword ───────────────────
    (("qualification", "qualifying",
      "clasificatoria", "clasificacion",
      "eliminatoria",),
     "qualification",      "📋 Clasificatoria"),

    # ── Nations League group stage (before generic "group stage") ────────────
    (("nations league", "liga de naciones",),
     "nations_league",     "🌍 Nations League"),

    # ── Tournament group stage (Champions, Copa América, etc.) ───────────────
    (("group stage", "group a", "group b", "group c", "group d",
      "group e", "group f", "group g", "group h",
      "group i", "group j", "group k", "group l",
      "group 1", "group 2", "group 3", "group 4",
      "league a", "league b", "league c", "league d",),
     "group_stage",        "🏟️ Fase de Grupos"),

    # ── Regular season / domestic league matchdays (lowest priority) ──────────
    (("matchday", "regular season", "regular-season",
      "clausura", "apertura", "jornada", "week ", "giornata",),
     "regular",            "📅 Fase Regular"),
]

# ── Prediction modifiers per stage ────────────────────────────────────────────
# (xg_multiplier, home_advantage_multiplier, btts_adjustment_pct)
#
# xg_multiplier         < 1.0  → knockout/pressure reduces expected goals
# home_advantage_multiplier < 1.0 → neutral venue / lower crowd effect
# btts_adjustment_pct   < 0.0  → reduces BTTS probability (percentage points)
_STAGE_MODIFIERS: dict[str, tuple[float, float, float]] = {
    # ── Knockout ──────────────────────────────────────────────────────────
    "final":                    (0.87, 0.92, -10.0),  # neutral venue, cagiest match
    "super_bowl":               (0.90, 0.92,  -7.0),
    "championship_final":       (0.88, 0.93,  -8.0),
    "nations_league_final_four":(0.89, 0.93,  -7.0),
    "semi_final":               (0.90, 0.95,  -6.0),
    "quarter_final":            (0.93, 0.97,  -4.0),
    "round_of_16":              (0.95, 0.98,  -2.0),
    "round_of_32":              (0.97, 0.99,  -1.0),
    "qual_playoff":             (0.94, 0.97,  -3.0),  # qualification play-offs
    "playoff_wild":             (0.95, 0.97,  -3.0),
    "playoffs":                 (0.93, 0.96,  -5.0),
    "liguilla":                 (0.91, 0.95,  -5.0),  # Liga MX Liguilla
    "repechaje":                (0.95, 0.98,  -2.0),
    "second_leg":               (0.93, 0.97,  -3.0),  # away-goals pressure
    # ── Qualification / classification ────────────────────────────────────
    "qualification":            (0.97, 0.99,  -1.0),  # early qualifying rounds
    "nations_league":           (0.97, 0.99,  -1.0),  # group stage of nations league
    "group_stage":              (0.98, 1.00,   0.0),  # tournament group stage
    # ── Regular / no adjustment ───────────────────────────────────────────
    "regular":                  (1.00, 1.00,   0.0),
}

# ── Importance score (higher = more prestigious; used for display sorting) ────
_STAGE_IMPORTANCE: dict[str, int] = {
    "final":                    10,
    "super_bowl":               10,
    "championship_final":       10,
    "nations_league_final_four":9,
    "semi_final":                8,
    "quarter_final":             7,
    "round_of_16":               6,
    "round_of_32":               5,
    "qual_playoff":              5,
    "playoff_wild":              6,
    "playoffs":                  5,
    "liguilla":                  7,
    "repechaje":                 4,
    "second_leg":                4,
    # classification
    "qualification":             3,
    "nations_league":            3,
    "group_stage":               3,
    # regular
    "regular":                   1,
}

# ── Stage category sets ────────────────────────────────────────────────────────
_KNOCKOUT_STAGES: frozenset[str] = frozenset({
    "final", "super_bowl", "championship_final", "nations_league_final_four",
    "semi_final", "quarter_final", "round_of_16", "round_of_32",
    "qual_playoff", "playoff_wild", "playoffs", "liguilla", "second_leg",
})

_QUALIFICATION_STAGES: frozenset[str] = frozenset({
    "qualification", "nations_league", "group_stage",
    # qual_playoff is both a qualification and a knockout-style elimination match
    "qual_playoff",
    # repechaje is a classification play-in (not a direct-elimination knockout)
    "repechaje",
})


def detect_stage(round_str: str) -> tuple[str, str]:
    """
    Detect the tournament stage from an API-Football ``round`` string or
    an ESPN game / series name.

    Parameters
    ----------
    round_str : str
        The raw round/series string, e.g. ``"Round of 16 - 1st Leg"``,
        ``"Semi-finals - 2nd Leg"``, ``"Final"``,
        ``"3rd Qualifying Round"``, ``"Group A - Matchday 2"``,
        or an ESPN game name such as
        ``"NBA Playoffs - Conference Finals - Game 3"``.

    Returns
    -------
    (stage_key, display_label) : tuple[str, str]
        ``stage_key``    — canonical key used for modifier look-up
                           (e.g. ``"semi_final"``); ``""`` when unknown.
        ``display_label``— localised Spanish label with emoji
                           (e.g. ``"🔥 Semifinal"``); ``""`` for regular.
    """
    if not round_str:
        return "", ""

    text = round_str.lower().strip()

    for keywords, key, label in _STAGE_PATTERNS:
        if any(kw in text for kw in keywords):
            # Return empty label for regular-season — no badge needed
            if key == "regular":
                return "regular", ""
            return key, label

    return "", ""


def get_stage_modifiers(stage_key: str) -> tuple[float, float, float]:
    """
    Return ``(xg_multiplier, home_advantage_multiplier, btts_adjustment_pct)``
    for *stage_key*.  Defaults to no adjustment when the key is unknown/empty.
    """
    return _STAGE_MODIFIERS.get(stage_key, (1.0, 1.0, 0.0))


def is_knockout_stage(stage_key: str) -> bool:
    """Return ``True`` when *stage_key* is a knockout (elimination) stage."""
    return stage_key in _KNOCKOUT_STAGES


def is_qualification(stage_key: str) -> bool:
    """Return ``True`` when *stage_key* is a qualification / classification stage."""
    return stage_key in _QUALIFICATION_STAGES


def stage_importance(stage_key: str) -> int:
    """Numeric importance score for *stage_key* (higher = more prestigious)."""
    return _STAGE_IMPORTANCE.get(stage_key, 1)
