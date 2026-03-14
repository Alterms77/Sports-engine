import csv
from pathlib import Path
from collections import defaultdict

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