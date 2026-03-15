import csv
from pathlib import Path
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "matches.csv"


def load_team_stats():
    teams = defaultdict(lambda: {"attack": 0.0, "defense": 0.0, "games": 0})

    with open(DATA_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            home = row["home_team"]
            away = row["away_team"]

            hg = int(row["home_goals"])
            ag = int(row["away_goals"])

            teams[home]["attack"] += hg
            teams[home]["defense"] += ag
            teams[home]["games"] += 1

            teams[away]["attack"] += ag
            teams[away]["defense"] += hg
            teams[away]["games"] += 1

    final = {}
    for team, s in teams.items():
        if s["games"] >= 1:
            final[team] = {
                "attack": round(s["attack"] / s["games"], 2),
                "defense": round(s["defense"] / s["games"], 2)
            }

    return final


# ===============================
# NUEVA FUNCIÓN
# PROMEDIO DE GOLES DE LA LIGA
# ===============================
def league_average_goals():

    total_goals = 0
    total_games = 0

    with open(DATA_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:

            hg = int(row["home_goals"])
            ag = int(row["away_goals"])

            total_goals += hg + ag
            total_games += 1

    # goles por equipo por partido
    return (total_goals / total_games) / 2


# ===============================
# NUEVA FUNCIÓN
# ESTADÍSTICAS DE FORMA RECIENTE
# ===============================
def recent_team_stats(last_games=10):

    teams = defaultdict(lambda: {"attack": 0.0, "defense": 0.0, "games": 0})

    with open(DATA_PATH, encoding="utf-8") as f:

        reader = list(csv.DictReader(f))

        # tomar partidos más recientes
        recent_matches = reader[-(last_games * 20):]

        for row in recent_matches:

            home = row["home_team"]
            away = row["away_team"]

            hg = int(row["home_goals"])
            ag = int(row["away_goals"])

            teams[home]["attack"] += hg
            teams[home]["defense"] += ag
            teams[home]["games"] += 1

            teams[away]["attack"] += ag
            teams[away]["defense"] += hg
            teams[away]["games"] += 1

    final = {}

    for team, s in teams.items():

        if s["games"] >= 3:

            final[team] = {
                "attack": round(s["attack"] / s["games"], 2),
                "defense": round(s["defense"] / s["games"], 2)
            }

    return final


# ===============================
# HOME / AWAY SPLIT STATISTICS
# ===============================

def load_home_away_stats() -> tuple:
    """
    Compute per-team home-specific and away-specific attack/defense averages.

    Returns (home_stats, away_stats) where each is a dict:
        {team: {"attack": float, "defense": float, "games": int}}

    home_stats[team]["attack"]  = avg goals scored  per home game
    home_stats[team]["defense"] = avg goals conceded per home game
    away_stats[team]["attack"]  = avg goals scored  per away game
    away_stats[team]["defense"] = avg goals conceded per away game

    Using split stats is the single most impactful accuracy improvement
    because home teams score ~29% more goals than away teams (1.51 vs 1.17).
    """
    home: dict = defaultdict(lambda: {"scored": 0.0, "conceded": 0.0, "games": 0})
    away: dict = defaultdict(lambda: {"scored": 0.0, "conceded": 0.0, "games": 0})

    with open(DATA_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ht = row["home_team"]
            at = row["away_team"]
            hg = int(row["home_goals"])
            ag = int(row["away_goals"])

            home[ht]["scored"] += hg
            home[ht]["conceded"] += ag
            home[ht]["games"] += 1

            away[at]["scored"] += ag
            away[at]["conceded"] += hg
            away[at]["games"] += 1

    home_stats = {}
    away_stats = {}

    for team, s in home.items():
        if s["games"] >= 3:
            home_stats[team] = {
                "attack": round(s["scored"] / s["games"], 3),
                "defense": round(s["conceded"] / s["games"], 3),
                "games": s["games"],
            }

    for team, s in away.items():
        if s["games"] >= 3:
            away_stats[team] = {
                "attack": round(s["scored"] / s["games"], 3),
                "defense": round(s["conceded"] / s["games"], 3),
                "games": s["games"],
            }

    return home_stats, away_stats


def league_home_away_averages() -> tuple:
    """
    Returns (avg_home_goals, avg_away_goals) per game from the full dataset.
    Used as normalization denominators in the split-stats xG formula.
    """
    total_home = total_away = total_games = 0
    with open(DATA_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_home += int(row["home_goals"])
            total_away += int(row["away_goals"])
            total_games += 1

    if total_games == 0:
        return 1.5, 1.0
    return total_home / total_games, total_away / total_games


# ===============================
# MATCH HISTORY (for form + streaks)
# ===============================

def load_match_history() -> dict:
    """
    Build per-team ordered match history for form and streak analysis.

    Each entry: {"scored": int, "conceded": int, "result": "W"/"D"/"L", "is_home": bool}
    Order in the CSV is treated as roughly chronological.

    Returns {team_name: [match_dict, ...]}
    """
    history: dict = defaultdict(list)

    with open(DATA_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ht = row["home_team"]
            at = row["away_team"]
            hg = int(row["home_goals"])
            ag = int(row["away_goals"])

            if hg > ag:
                hr, ar = "W", "L"
            elif hg == ag:
                hr = ar = "D"
            else:
                hr, ar = "L", "W"

            history[ht].append(
                {"scored": hg, "conceded": ag, "result": hr, "is_home": True}
            )
            history[at].append(
                {"scored": ag, "conceded": hg, "result": ar, "is_home": False}
            )

    return dict(history)


# ===============================
# HEAD-TO-HEAD DATA
# ===============================

def load_h2h_data() -> dict:
    """
    Build a head-to-head lookup from all dataset matches.

    Returns {(home_team, away_team): [(hg, ag), ...]}
    Keyed by the *exact* dataset team names after resolution.
    """
    h2h: dict = defaultdict(list)

    with open(DATA_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["home_team"], row["away_team"])
            h2h[key].append((int(row["home_goals"]), int(row["away_goals"])))

    return dict(h2h)