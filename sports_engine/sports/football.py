from core.teams import normalize_team as normalize_from_teams
from core.stats import load_team_stats, league_average_goals, recent_team_stats
from core.probabilities import match_probabilities
from core.scorelines import top_scorelines
from core.corners import expected_corners
from core.cards import expected_cards
from core.confidence import confidence_level
from core.simulation import simulate_scoreline
from core.value import detect_value_bets
import unicodedata

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
# NUEVA UTILIDAD
# NORMALIZADOR FUERTE DE EQUIPOS
# ===============================
def clean_team_name(name: str):

    name = strip_accents(name).lower()

    words_to_remove = [
        "fc", "cf", "club", "sc", "afc", "ac",
        "cd", "sd", "ud", "rc", "bk", "fk"
    ]

    for w in words_to_remove:
        name = name.replace(w, "")

    name = name.replace(".", "")
    name = name.replace("-", " ")
    name = name.replace("_", " ")

    name = " ".join(name.split())

    return name


# ===============================
# 🧠 NORMALIZACIÓN FINAL
# ===============================
def resolve_team(name: str) -> str | None:

    canonical = normalize_from_teams(name)

    # si no lo encuentra en teams.py seguimos con el nombre original
    if not canonical:
        canonical = name

    canonical_clean = clean_team_name(canonical)

    for team in TEAM_STATS:

        team_clean = clean_team_name(team)

        # coincidencia exacta
        if canonical_clean == team_clean:
            return team

        # coincidencia parcial
        if canonical_clean in team_clean or team_clean in canonical_clean:
            return team

    return None


# ===============================
# ⚽ GOLES ESPERADOS (MEJORADO)
# ===============================
def expected_goals(home, away):

    h = TEAM_STATS[home]
    a = TEAM_STATS[away]

    attack_home = h["attack"]
    defense_home = h["defense"]

    attack_away = a["attack"]
    defense_away = a["defense"]

    recent_h = RECENT_STATS.get(home)
    recent_a = RECENT_STATS.get(away)

    if recent_h:
        attack_home = 0.7 * recent_h["attack"] + 0.3 * attack_home
        defense_home = 0.7 * recent_h["defense"] + 0.3 * defense_home

    if recent_a:
        attack_away = 0.7 * recent_a["attack"] + 0.3 * attack_away
        defense_away = 0.7 * recent_a["defense"] + 0.3 * defense_away

    attack_home /= LEAGUE_AVG
    defense_home /= LEAGUE_AVG

    attack_away /= LEAGUE_AVG
    defense_away /= LEAGUE_AVG

    home_advantage = 1.10

    xg_home = attack_home * defense_away * LEAGUE_AVG * home_advantage
    xg_away = attack_away * defense_home * LEAGUE_AVG

    return round(xg_home, 2), round(xg_away, 2)


# ===============================
# 🔮 PREDICCIÓN PRINCIPAL
# ===============================
def predict_match(home, away):

    home = resolve_team(home)
    away = resolve_team(away)

    if not home or not away:
        raise ValueError("Equipo no encontrado en la base de datos")

    xg_home, xg_away = expected_goals(home, away)

    probs = match_probabilities(xg_home, xg_away)

    simulation = simulate_scoreline(xg_home, xg_away)

    odds = {
        "home": 2.10,
        "draw": 3.30,
        "away": 3.60
    }

    value = detect_value_bets({
        "home_win": simulation["home_win_prob"],
        "draw": simulation["draw_prob"],
        "away_win": simulation["away_win_prob"]
    }, odds)

    return {
        "home": home,
        "away": away,

        "xg_home": xg_home,
        "xg_away": xg_away,

        "home_win": simulation["home_win_prob"],
        "draw": simulation["draw_prob"],
        "away_win": simulation["away_win_prob"],

        "over_1_5": probs["over_1_5"],
        "over_2_5": probs["over_2_5"],
        "over_3_5": probs["over_3_5"],
        "btts": probs["btts"],

        "top_scores": top_scorelines(xg_home, xg_away),

        "sim_home_goals": simulation["avg_home_goals"],
        "sim_away_goals": simulation["avg_away_goals"],

        "corners": expected_corners(xg_home, xg_away),
        "cards": expected_cards(xg_home, xg_away),

        "value_bets": value,

        "confidence": confidence_level(probs)
    }


# ===============================
# 🌐 FUNCIÓN PÚBLICA
# ===============================
def get_full_prediction(home, away):
    return predict_match(home, away)