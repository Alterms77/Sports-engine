import requests
import csv
import os
import sys
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL

headers = {
    "x-apisports-key": API_SPORTS_KEY
}

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "matches.csv"

url = f"{API_SPORTS_BASE_URL}/fixtures"

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