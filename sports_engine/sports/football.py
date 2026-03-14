import math
import unicodedata
import logging

from core.teams import normalize_team as normalize_from_teams
from core.stats import (
    load_team_stats,
    league_average_goals,
    recent_team_stats,
    load_home_away_stats,
    league_home_away_averages,
    load_match_history,
    load_h2h_data,
)
from core.probabilities import match_probabilities
from core.scorelines import top_scorelines
from core.corners import expected_corners
from core.cards import expected_cards
from core.confidence import confidence_level
from core.simulation import simulate_scoreline
from core.value import detect_value_bets
from core.form import (
    decay_weighted_stats,
    current_streak,
    form_emoji,
    h2h_adjustment,
    h2h_summary,
    clean_sheet_prob,
)
from core.config import HOME_ADVANTAGE, detect_league
from core.distributions import poisson_pmf

logger = logging.getLogger(__name__)

# ── Load all datasets at import time (cached for the process lifetime) ──
TEAM_STATS = load_team_stats()
LEAGUE_AVG = league_average_goals()
HOME_STATS, AWAY_STATS = load_home_away_stats()
LEAGUE_HOME_AVG, LEAGUE_AWAY_AVG = league_home_away_averages()
MATCH_HISTORY = load_match_history()
H2H_DATA = load_h2h_data()

logger.info(
    "Stats loaded — %d teams, home_avg=%.3f, away_avg=%.3f",
    len(TEAM_STATS), LEAGUE_HOME_AVG, LEAGUE_AWAY_AVG,
)


# ===============================
# 🔤 TEXT UTILITIES
# ===============================

def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def clean_team_name(name: str) -> str:
    name = strip_accents(name).lower()
    for w in ["fc", "cf", "club", "sc", "afc", "ac", "cd", "sd", "ud", "rc", "bk", "fk"]:
        name = name.replace(w, "")
    name = name.replace(".", "").replace("-", " ").replace("_", " ")
    return " ".join(name.split())


# ===============================
# 🧠 TEAM NAME RESOLUTION
# ===============================

def resolve_team(name: str) -> "str | None":
    """Return the canonical dataset team name, or None if not found."""
    canonical = normalize_from_teams(name) or name
    canonical_clean = clean_team_name(canonical)
    input_clean = clean_team_name(name)

    candidates = list({canonical_clean, input_clean})
    best_match = None
    best_score = 0.0

    for team in TEAM_STATS:
        team_clean = clean_team_name(team)
        for query in candidates:
            if query == team_clean:
                return team
            if query in team_clean or team_clean in query:
                return team
            query_words = set(query.split())
            team_words = set(team_clean.split())
            if query_words and team_words:
                overlap = len(query_words & team_words)
                score = overlap / max(len(query_words), len(team_words))
                if score > best_score:
                    best_score = score
                    best_match = team

    if best_score >= 0.5:
        return best_match
    return None


def suggest_teams(name: str, top_n: int = 3) -> list:
    """Return up to top_n dataset team names closest to the given name."""
    name_clean = clean_team_name(name)
    name_words = name_clean.split()
    scores = []
    for team in TEAM_STATS:
        team_clean = clean_team_name(team)
        score = 0
        for w in name_words:
            for tw in team_clean.split():
                if w == tw:
                    score += 2
                elif len(w) >= 3 and (tw.startswith(w) or w.startswith(tw)):
                    score += 1
        if score > 0:
            scores.append((team, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scores[:top_n]]


# ===============================
# DIXON-COLES RHO CORRECTION
# ===============================

_RHO = -0.13  # standard estimate from Dixon & Coles (1997)


def _dc_correction(
    home_goals: int, away_goals: int, xg_home: float, xg_away: float
) -> float:
    """
    Dixon-Coles tau correction for low-scoring outcomes.
    Adjusts (0,0), (1,0), (0,1), (1,1) which are systematically mispriced
    by independent Poisson.
    """
    rho = _RHO
    if home_goals == 0 and away_goals == 0:
        return 1.0 - xg_home * xg_away * rho
    elif home_goals == 1 and away_goals == 0:
        return 1.0 + xg_away * rho
    elif home_goals == 0 and away_goals == 1:
        return 1.0 + xg_home * rho
    elif home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


# ===============================
# ⚽ EXPECTED GOALS — HOME/AWAY SPLIT MODEL
# ===============================

def expected_goals(
    home_name: str, away_name: str, league: str = "default"
) -> tuple:
    """
    Compute expected goals using home/away split statistics.

    Primary formula (when split data available):
      xg_home = home_team_home_attack * (away_team_away_defense / LEAGUE_HOME_AVG)
      xg_away = away_team_away_attack * (home_team_home_defense / LEAGUE_AWAY_AVG)

    This captures home advantage naturally: home teams average 1.508 goals at home
    vs 1.169 goals when away (29% difference). No explicit home_advantage multiplier
    is needed when using split stats — it's already embedded in the data.

    For teams without enough split data, falls back to combined stats with an
    explicit home advantage multiplier (league-specific from config).

    Form and H2H adjustments are applied on top.
    """
    h_home = HOME_STATS.get(home_name)
    a_away = AWAY_STATS.get(away_name)

    if h_home and a_away and LEAGUE_HOME_AVG > 0 and LEAGUE_AWAY_AVG > 0:
        # ── Primary model: home/away split (most accurate) ──
        # xg_home: home team scoring at home × how hard away team's away defense is
        # Normalization: away team's away defense conceded / avg goals scored by home teams
        xg_home = h_home["attack"] * (a_away["defense"] / LEAGUE_HOME_AVG)
        # xg_away: away team scoring away × how hard home team's home defense is
        # Normalization: home team's home defense conceded / avg goals scored by away teams
        xg_away = a_away["attack"] * (h_home["defense"] / LEAGUE_AWAY_AVG)
    else:
        # ── Fallback: combined stats + explicit home advantage ──
        h = TEAM_STATS.get(home_name, {"attack": 1.5, "defense": 1.2})
        a = TEAM_STATS.get(away_name, {"attack": 1.0, "defense": 1.5})
        league_avg = max(LEAGUE_AVG, 0.01)
        home_adv = HOME_ADVANTAGE.get(league, HOME_ADVANTAGE["default"])
        norm_h_att = h["attack"] / league_avg
        norm_a_def = a["defense"] / league_avg
        norm_a_att = a["attack"] / league_avg
        norm_h_def = h["defense"] / league_avg
        xg_home = norm_h_att * norm_a_def * league_avg * home_adv
        xg_away = norm_a_att * norm_h_def * league_avg

    # ── Form adjustment: exponential-decay weighted attack ratio ──
    home_history = MATCH_HISTORY.get(home_name)
    away_history = MATCH_HISTORY.get(away_name)

    home_form = decay_weighted_stats(home_history) if home_history else None
    away_form = decay_weighted_stats(away_history) if away_history else None

    h_combined = TEAM_STATS.get(home_name)
    a_combined = TEAM_STATS.get(away_name)

    if home_form and h_combined and h_combined["attack"] > 0:
        # How much above/below their season average the home team is scoring lately
        form_attack_ratio = home_form["attack"] / h_combined["attack"]
        # Dampen: 70% base + 30% form; cap ratio at 2x to prevent outliers
        xg_home *= 0.70 + 0.30 * min(form_attack_ratio, 2.0)

    if away_form and a_combined and a_combined["attack"] > 0:
        form_attack_ratio = away_form["attack"] / a_combined["attack"]
        xg_away *= 0.70 + 0.30 * min(form_attack_ratio, 2.0)

    # ── Streak momentum multiplier ──
    home_streak = current_streak(home_history) if home_history else {"multiplier": 1.0}
    away_streak = current_streak(away_history) if away_history else {"multiplier": 1.0}
    xg_home *= home_streak["multiplier"]
    xg_away *= away_streak["multiplier"]

    # ── H2H adjustment ──
    h2h_records = H2H_DATA.get((home_name, away_name), [])
    h2h_mult = h2h_adjustment(h2h_records)
    xg_home *= h2h_mult

    return round(max(xg_home, 0.10), 2), round(max(xg_away, 0.10), 2)


# ===============================
# 📊 DIXON-COLES CORRECTED PROBABILITIES
# ===============================

def dixon_coles_probabilities(
    xg_home: float, xg_away: float, max_goals: int = 8
) -> dict:
    """
    1X2 + Over/BTTS probabilities with Dixon-Coles tau correction.
    Adjusts the independent-Poisson assumption for low-scoring outcomes.
    """
    home_win = draw = away_win = 0.0
    over_1_5 = over_2_5 = over_3_5 = btts = 0.0

    for hg in range(max_goals + 1):
        for ag in range(max_goals + 1):
            p = poisson_pmf(hg, xg_home) * poisson_pmf(ag, xg_away)
            p *= _dc_correction(hg, ag, xg_home, xg_away)

            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p

            total = hg + ag
            if total > 1:
                over_1_5 += p
            if total > 2:
                over_2_5 += p
            if total > 3:
                over_3_5 += p
            if hg > 0 and ag > 0:
                btts += p

    # Re-normalise (DC correction slightly shifts total probability mass)
    total_p = home_win + draw + away_win
    if total_p > 0:
        home_win /= total_p
        draw /= total_p
        away_win /= total_p

    return {
        "home_win": round(home_win * 100, 1),
        "draw": round(draw * 100, 1),
        "away_win": round(away_win * 100, 1),
        "over_1_5": round(over_1_5 * 100, 1),
        "over_2_5": round(over_2_5 * 100, 1),
        "over_3_5": round(over_3_5 * 100, 1),
        "btts": round(btts * 100, 1),
    }


# ===============================
# 🔮 PREDICTION CORE
# ===============================

def predict_match(
    home: str,
    away: str,
    league: str = "default",
    odds: dict = None,
) -> dict:
    home_resolved = resolve_team(home)
    away_resolved = resolve_team(away)

    if not home_resolved:
        raise ValueError(f"Equipo '{home}' no encontrado en la base de datos")
    if not away_resolved:
        raise ValueError(f"Equipo '{away}' no encontrado en la base de datos")

    # Auto-detect league if not explicitly provided
    if league == "default":
        league = detect_league(home_resolved, away_resolved)

    xg_home, xg_away = expected_goals(home_resolved, away_resolved, league)

    # Dixon-Coles analytical probabilities
    probs = dixon_coles_probabilities(xg_home, xg_away)

    # Monte Carlo simulation (50k runs) for additional stability
    simulation = simulate_scoreline(xg_home, xg_away)

    # Blend: 60% DC analytical + 40% Monte Carlo
    blended_home = 0.6 * probs["home_win"] + 0.4 * simulation["home_win_prob"]
    blended_draw = 0.6 * probs["draw"] + 0.4 * simulation["draw_prob"]
    blended_away = 0.6 * probs["away_win"] + 0.4 * simulation["away_win_prob"]

    final_probs = {
        "home_win": round(blended_home, 1),
        "draw": round(blended_draw, 1),
        "away_win": round(blended_away, 1),
        "over_1_5": probs["over_1_5"],
        "over_2_5": probs["over_2_5"],
        "over_3_5": probs["over_3_5"],
        "btts": probs["btts"],
    }

    # Value bets (only when user provides odds)
    value = {}
    if odds:
        value = detect_value_bets(
            {
                "home_win": final_probs["home_win"],
                "draw": final_probs["draw"],
                "away_win": final_probs["away_win"],
            },
            odds,
        )

    # ── Form & streak context ──
    home_history = MATCH_HISTORY.get(home_resolved, [])
    away_history = MATCH_HISTORY.get(away_resolved, [])
    home_streak = current_streak(home_history)
    away_streak = current_streak(away_history)

    # ── H2H context ──
    h2h_records = H2H_DATA.get((home_resolved, away_resolved), [])
    h2h_info = h2h_summary(h2h_records)

    # ── Clean sheet probabilities ──
    h_home_data = HOME_STATS.get(home_resolved)
    a_away_data = AWAY_STATS.get(away_resolved)
    cs_home = clean_sheet_prob(h_home_data["defense"]) if h_home_data else None
    cs_away = clean_sheet_prob(a_away_data["defense"]) if a_away_data else None

    return {
        "home": home_resolved,
        "away": away_resolved,
        "league": league,
        "xg_home": xg_home,
        "xg_away": xg_away,
        "home_win": final_probs["home_win"],
        "draw": final_probs["draw"],
        "away_win": final_probs["away_win"],
        "over_1_5": final_probs["over_1_5"],
        "over_2_5": final_probs["over_2_5"],
        "over_3_5": final_probs["over_3_5"],
        "btts": final_probs["btts"],
        "top_scores": top_scorelines(xg_home, xg_away),
        "sim_home_goals": simulation["avg_home_goals"],
        "sim_away_goals": simulation["avg_away_goals"],
        "corners": expected_corners(xg_home, xg_away),
        "cards": expected_cards(xg_home, xg_away),
        "value_bets": value,
        "confidence": confidence_level(final_probs),
        # ── Intelligence context for display ──
        "form_home": {
            "emoji": form_emoji(home_streak),
            "last5": home_streak["last5"],
            "streak": home_streak,
        },
        "form_away": {
            "emoji": form_emoji(away_streak),
            "last5": away_streak["last5"],
            "streak": away_streak,
        },
        "h2h": h2h_info,
        "clean_sheet_home": cs_home,
        "clean_sheet_away": cs_away,
    }


# ===============================
# 🌐 PUBLIC API
# ===============================

def get_full_prediction(
    home: str,
    away: str,
    league: str = "default",
    odds: dict = None,
) -> dict:
    return predict_match(home, away, league=league, odds=odds)


def get_team_stats_summary(team_name: str) -> dict:
    """
    Return a display-ready stats summary for a single team.
    Used by the /stats bot command.
    """
    resolved = resolve_team(team_name)
    if not resolved:
        return {}

    history = MATCH_HISTORY.get(resolved, [])
    streak = current_streak(history)
    h_home = HOME_STATS.get(resolved, {})
    h_away = AWAY_STATS.get(resolved, {})
    combined = TEAM_STATS.get(resolved, {})

    return {
        "name": resolved,
        "home_attack": h_home.get("attack"),
        "home_defense": h_home.get("defense"),
        "home_games": h_home.get("games"),
        "away_attack": h_away.get("attack"),
        "away_defense": h_away.get("defense"),
        "away_games": h_away.get("games"),
        "combined_attack": combined.get("attack"),
        "combined_defense": combined.get("defense"),
        "form_emoji": form_emoji(streak),
        "last5": streak["last5"],
        "streak": streak,
        "cs_home_prob": clean_sheet_prob(h_home["defense"]) if h_home else None,
        "cs_away_prob": clean_sheet_prob(h_away["defense"]) if h_away else None,
        "league": detect_league(resolved, resolved),
    }


# ===============================
# 🔤 UTILIDAD: quitar acentos
# ===============================
def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


# ===============================
# NORMALIZADOR FUERTE DE EQUIPOS
# ===============================
def clean_team_name(name: str) -> str:
    name = strip_accents(name).lower()
    words_to_remove = ["fc", "cf", "club", "sc", "afc", "ac", "cd", "sd", "ud", "rc", "bk", "fk"]
    for w in words_to_remove:
        name = name.replace(w, "")
    name = name.replace(".", "").replace("-", " ").replace("_", " ")
    return " ".join(name.split())


# ===============================
# 🧠 NORMALIZACIÓN FINAL
# ===============================
def resolve_team(name: str) -> "str | None":
    """Return the canonical team name from TEAM_STATS, or None."""
    canonical = normalize_from_teams(name) or name
    canonical_clean = clean_team_name(canonical)
    input_clean = clean_team_name(name)

    # Build candidate pool: both the normalized name and the raw input
    candidates = list({canonical_clean, input_clean})

    best_match = None
    best_score = 0

    for team in TEAM_STATS:
        team_clean = clean_team_name(team)

        for query in candidates:
            # Exact match
            if query == team_clean:
                return team

            # Substring match
            if query in team_clean or team_clean in query:
                return team

            # Word-overlap scoring (handles "Guadalajara" ↔ "Guadalajara Chivas")
            query_words = set(query.split())
            team_words = set(team_clean.split())
            if query_words and team_words:
                overlap = len(query_words & team_words)
                score = overlap / max(len(query_words), len(team_words))
                if score > best_score:
                    best_score = score
                    best_match = team

    # Accept word-overlap match if at least 50% of words match
    if best_score >= 0.5:
        return best_match

    return None


def suggest_teams(name: str, top_n: int = 3) -> list:
    """Return up to top_n dataset team names closest to the given name."""
    name_clean = clean_team_name(name)
    name_words = name_clean.split()
    scores = []
    for team in TEAM_STATS:
        team_clean = clean_team_name(team)
        score = 0
        for w in name_words:
            for tw in team_clean.split():
                if w == tw:
                    score += 2
                elif len(w) >= 3 and (tw.startswith(w) or w.startswith(tw)):
                    score += 1
        if score > 0:
            scores.append((team, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scores[:top_n]]
