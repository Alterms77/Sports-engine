import csv
from sports.football import simulate_context_match


def load_matches(path):
    matches = []

    with open(path, encoding="utf-8-sig", newline="") as f:
        # Detecta automáticamente el separador
        sample = f.read(1024)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample)
        reader = csv.DictReader(f, dialect=dialect)

        for row in reader:
            # Normalizar llaves: minúsculas, sin espacios
            clean_row = {
                k.strip().lower(): v.strip()
                for k, v in row.items()
                if k is not None
            }
            matches.append(clean_row)

    return matches


def get_match_result(home_goals, away_goals):
    if home_goals > away_goals:
        return "HOME"
    elif home_goals < away_goals:
        return "AWAY"
    else:
        return "DRAW"


def backtest(matches, team_stats):
    correct = 0
    total = 0

    for m in matches:
        # DEBUG de seguridad (puedes borrar luego)
        required = {"home", "away", "home_goals", "away_goals", "competition"}
        if not required.issubset(m.keys()):
            print("⚠️ Fila ignorada, columnas encontradas:", m.keys())
            continue

        home = m["home"]
        away = m["away"]

        if home not in team_stats or away not in team_stats:
            continue

        result_real = get_match_result(
            int(m["home_goals"]),
            int(m["away_goals"])
        )

        stats_home = team_stats[home]
        stats_away = team_stats[away]

        simulation = simulate_context_match(
            home_attack=stats_home["attack"],
            home_defense=stats_home["defense"],
            away_attack=stats_away["attack"],
            away_defense=stats_away["defense"],
            home_recent_goals=stats_home["recent_goals"],
            away_recent_goals=stats_away["recent_goals"],
            competition=m["competition"]
        )

        predicted = max(
            simulation["prob_results"],
            key=simulation["prob_results"].get
        )

        if predicted == result_real:
            correct += 1

        total += 1

    accuracy = correct / total if total > 0 else 0
    return correct, total, accuracy