import math
import unicodedata
import logging

from core.teams import normalize_team as normalize_from_teams
from core.stats import load_team_stats, league_average_goals, recent_team_stats
from core.probabilities import match_probabilities
from core.scorelines import top_scorelines
from core.corners import expected_corners
from core.cards import expected_cards
from core.confidence import confidence_level
from core.simulation import simulate_scoreline
from core.value import detect_value_bets
from core.config import HOME_ADVANTAGE

logger = logging.getLogger(__name__)

TEAM_STATS = load_team_stats()
LEAGUE_AVG = league_average_goals()
RECENT_STATS = recent_team_stats()

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
    """Return up to top_n team names that are close to the given name."""
    name_clean = clean_team_name(name)
    name_words = name_clean.split()
    scores = []
    for team in TEAM_STATS:
        team_clean = clean_team_name(team)
        score = 0
        # Word overlap
        team_words = team_clean.split()
        for w in name_words:
            for tw in team_words:
                # Full word match
                if w == tw:
                    score += 2
                # Prefix match (handles "Manch" → "Manchester")
                elif len(w) >= 3 and (tw.startswith(w) or w.startswith(tw)):
                    score += 1
        if score > 0:
            scores.append((team, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scores[:top_n]]


# ===============================
# Dixon-Coles rho correction
# ===============================
_RHO = -0.13  # standard estimate from Dixon & Coles (1997)


def _dc_correction(home_goals: int, away_goals: int, xg_home: float, xg_away: float) -> float:
    """
    Apply the Dixon-Coles tau correction for low-scoring outcomes.
    Corrects the independent-Poisson assumption for scores (0,0), (1,0), (0,1), (1,1).
    """
    rho = _RHO
    mu = xg_home
    nu = xg_away
    if home_goals == 0 and away_goals == 0:
        return 1.0 - mu * nu * rho
    elif home_goals == 1 and away_goals == 0:
        return 1.0 + nu * rho
    elif home_goals == 0 and away_goals == 1:
        return 1.0 + mu * rho
    elif home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


# ===============================
# ⚽ GOLES ESPERADOS (MEJORADO)
# Exponential-decay form weighting + league home advantage
# ===============================
def expected_goals(home: str, away: str, league: str = "default") -> tuple:
    h = TEAM_STATS[home]
    a = TEAM_STATS[away]

    attack_home = h["attack"]
    defense_home = h["defense"]
    attack_away = a["attack"]
    defense_away = a["defense"]

    # Exponential-decay form: weight recent stats 70/30 vs season average
    recent_h = RECENT_STATS.get(home)
    recent_a = RECENT_STATS.get(away)

    if recent_h:
        attack_home = 0.70 * recent_h["attack"] + 0.30 * attack_home
        defense_home = 0.70 * recent_h["defense"] + 0.30 * defense_home

    if recent_a:
        attack_away = 0.70 * recent_a["attack"] + 0.30 * attack_away
        defense_away = 0.70 * recent_a["defense"] + 0.30 * defense_away

    # Normalize by league average
    league_avg = max(LEAGUE_AVG, 0.01)
    attack_home /= league_avg
    defense_home /= league_avg
    attack_away /= league_avg
    defense_away /= league_avg

    # League-specific home advantage
    home_adv = HOME_ADVANTAGE.get(league, HOME_ADVANTAGE["default"])

    xg_home = attack_home * defense_away * league_avg * home_adv
    xg_away = attack_away * defense_home * league_avg

    return round(max(xg_home, 0.1), 2), round(max(xg_away, 0.1), 2)


# ===============================
# 📊 DIXON-COLES CORRECTED PROBABILITIES
# ===============================
def dixon_coles_probabilities(xg_home: float, xg_away: float, max_goals: int = 8) -> dict:
    """
    Compute 1X2 + over/BTTS probabilities with Dixon-Coles tau correction
    applied to low-scoring outcomes.
    """
    from core.distributions import poisson_pmf

    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    over_1_5 = 0.0
    over_2_5 = 0.0
    over_3_5 = 0.0
    btts = 0.0

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

    # Re-normalize (DC correction slightly shifts total probability mass)
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
# 🔮 PREDICCIÓN PRINCIPAL
# ===============================
def predict_match(home: str, away: str, league: str = "default", odds: dict = None) -> dict:
    home_resolved = resolve_team(home)
    away_resolved = resolve_team(away)

    if not home_resolved:
        raise ValueError(f"Equipo '{home}' no encontrado en la base de datos")
    if not away_resolved:
        raise ValueError(f"Equipo '{away}' no encontrado en la base de datos")

    xg_home, xg_away = expected_goals(home_resolved, away_resolved, league)

    # Dixon-Coles corrected probabilities (analytical)
    probs = dixon_coles_probabilities(xg_home, xg_away)

    # Monte Carlo simulation (50k runs) for scoreline and prob stability
    simulation = simulate_scoreline(xg_home, xg_away)

    # Blend analytical DC probs (60%) with MC simulation (40%) for final 1X2
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

    # Value bets (only computed when odds are provided)
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

    return {
        "home": home_resolved,
        "away": away_resolved,
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
    }


# ===============================
# 🌐 FUNCIÓN PÚBLICA
# ===============================
def get_full_prediction(home: str, away: str, league: str = "default", odds: dict = None) -> dict:
    return predict_match(home, away, league=league, odds=odds)
