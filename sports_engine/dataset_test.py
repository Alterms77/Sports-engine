import csv
from collections import defaultdict
from pathlib import Path

DATA_PATH = Path("data/matches.csv")

teams = defaultdict(int)

total_matches = 0

with open(DATA_PATH, encoding="utf-8") as f:

    reader = csv.DictReader(f)

    for r in reader:

        home = r["home_team"]
        away = r["away_team"]

        teams[home] += 1
        teams[away] += 1

        total_matches += 1


print("\n==============================")
print("DATASET ANALYSIS")
print("==============================")

print("Total partidos:", total_matches)
print("Total equipos:", len(teams))


print("\nEquipos con menos de 3 partidos:\n")

low = 0

for team, games in sorted(teams.items(), key=lambda x: x[1]):

    if games < 3:
        print(team, "->", games)
        low += 1


print("\nTotal equipos con pocos partidos:", low)


print("\nTop equipos con más partidos:\n")

for team, games in sorted(teams.items(), key=lambda x: x[1], reverse=True)[:20]:
    print(team, "->", games)