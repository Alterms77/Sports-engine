import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "historical"
OUTPUT = BASE_DIR / "data" / "matches.csv"

rows = []

files = list(DATA_DIR.glob("*.csv"))

print("Archivos encontrados:", files)

for file in files:

    print("Procesando:", file.name)

    # usamos latin1 para evitar errores de encoding
    with open(file, encoding="latin1") as f:

        reader = csv.DictReader(f)

        for r in reader:

            home = (
                r.get("HomeTeam")
                or r.get("Home")
                or r.get("home_team")
            )

            away = (
                r.get("AwayTeam")
                or r.get("Away")
                or r.get("away_team")
            )

            hg = (
                r.get("FTHG")
                or r.get("HG")
                or r.get("home_goals")
            )

            ag = (
                r.get("FTAG")
                or r.get("AG")
                or r.get("away_goals")
            )

            if not home or not away:
                continue

            if not hg or not ag:
                continue

            rows.append([
                home.strip(),
                away.strip(),
                int(hg),
                int(ag)
            ])

with open(OUTPUT, "w", newline="", encoding="utf-8") as f:

    writer = csv.writer(f)

    writer.writerow([
        "home_team",
        "away_team",
        "home_goals",
        "away_goals"
    ])

    for r in rows:
        writer.writerow(r)

print("Dataset creado con", len(rows), "partidos")