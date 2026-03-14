import csv
from pathlib import Path
from api.football_data_api import get_matches

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "matches.csv"

COMPETITIONS = {
    "CL": "Champions League",
    "PL": "Premier League",
    "PD": "LaLiga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1"
}

def save_all_matches():
    rows = []

    for code, name in COMPETITIONS.items():
        print(f"📥 Descargando {name}...")
        matches = get_matches(code)

        for m in matches:
            if m["status"] != "FINISHED":
                continue

            rows.append({
                "home_team": m["homeTeam"]["name"],
                "away_team": m["awayTeam"]["name"],
                "home_goals": m["score"]["fullTime"]["home"],
                "away_goals": m["score"]["fullTime"]["away"],
                "competition": name,
                "date": m["utcDate"]
            })

    DATA_PATH.parent.mkdir(exist_ok=True)

    with open(DATA_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["home_team", "away_team", "home_goals", "away_goals", "competition", "date"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"✅ Partidos guardados: {len(rows)}")

if __name__ == "__main__":
    save_all_matches()
