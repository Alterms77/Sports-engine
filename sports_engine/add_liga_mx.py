import requests
import csv
from pathlib import Path

API_KEY = "326817dbace2d3e8eadc29be1d404a17"

headers = {
    "x-apisports-key": API_KEY
}

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "matches.csv"

url = "https://v3.football.api-sports.io/fixtures"

params = {
    "league": 262,   # Liga MX
    "season": 2025
}

r = requests.get(url, headers=headers, params=params)
data = r.json()

rows = []

for m in data["response"]:

    if m["goals"]["home"] is None:
        continue

    rows.append([
        m["teams"]["home"]["name"],
        m["teams"]["away"]["name"],
        m["goals"]["home"],
        m["goals"]["away"]
    ])

print("Partidos encontrados:", len(rows))

with open(DATA_PATH, "a", newline="", encoding="utf-8") as f:

    writer = csv.writer(f)

    for r in rows:
        writer.writerow(r)

print("Partidos agregados al dataset")