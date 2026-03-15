"""
Elo Rating System for Sports Engine.

Every team starts at 1500. After each match:
  - K-factor = 32 (adjustable per league importance)
  - Winner gains Elo points, loser loses them
  - Expected score = 1 / (1 + 10^((rating_b - rating_a) / 400))
  - Draw: both teams move toward 50% expected

Elo ratings are stored in a JSON file and updated after each finished match
from the CSV data. They persist between bot restarts.

The Elo difference is used to adjust xG in football.py:
  - Large Elo advantage (>200 pts) → xG boost up to +15%
  - Large Elo disadvantage → xG penalty up to -15%
"""

import json
import os
import csv
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

ELO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "elo_ratings.json",
)
DEFAULT_ELO = 1500
K_FACTOR = 32


def load_elo_ratings() -> Dict[str, float]:
    """Load Elo ratings from JSON file, return empty dict if not found."""
    if not os.path.exists(ELO_FILE):
        return {}
    try:
        with open(ELO_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: float(v) for k, v in data.items()}
    except Exception as exc:
        logger.warning("Could not load Elo ratings from %s: %s", ELO_FILE, exc)
    return {}


def save_elo_ratings(ratings: Dict[str, float]) -> None:
    """Save Elo ratings to JSON file."""
    os.makedirs(os.path.dirname(ELO_FILE), exist_ok=True)
    try:
        with open(ELO_FILE, "w", encoding="utf-8") as f:
            json.dump(ratings, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Could not save Elo ratings to %s: %s", ELO_FILE, exc)


def expected_score(rating_a: float, rating_b: float) -> float:
    """Expected score for player A given both ratings."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def update_elo(
    ratings: Dict[str, float],
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    k: float = K_FACTOR,
) -> Dict[str, float]:
    """Update ratings after a match. Returns the modified ratings dict."""
    home_rating = ratings.get(home, DEFAULT_ELO)
    away_rating = ratings.get(away, DEFAULT_ELO)

    exp_home = expected_score(home_rating, away_rating)
    exp_away = expected_score(away_rating, home_rating)

    if home_goals > away_goals:
        actual_home, actual_away = 1.0, 0.0
    elif home_goals < away_goals:
        actual_home, actual_away = 0.0, 1.0
    else:
        actual_home, actual_away = 0.5, 0.5

    ratings[home] = home_rating + k * (actual_home - exp_home)
    ratings[away] = away_rating + k * (actual_away - exp_away)
    return ratings


def elo_xg_adjustment(home_elo: float, away_elo: float) -> Tuple[float, float]:
    """
    Return (home_multiplier, away_multiplier) based on Elo difference.

    Elo diff > 200: stronger team gets up to +15% xG
    Elo diff > 100: up to +8% xG
    Elo diff < 50: negligible adjustment

    Maximum adjustment: ±15% (multiplier range: 0.85 to 1.15)
    """
    diff = home_elo - away_elo
    # Scale: 400 Elo point difference maps to the maximum ±15% xG adjustment
    adjustment = diff / 400 * 0.15
    adjustment = max(-0.15, min(0.15, adjustment))

    home_mult = 1.0 + adjustment
    away_mult = 1.0 - adjustment
    return round(home_mult, 3), round(away_mult, 3)


def build_elo_from_csv(csv_path: str) -> Dict[str, float]:
    """Process all historical matches from CSV to build initial Elo ratings."""
    ratings: Dict[str, float] = {}
    if not os.path.exists(csv_path):
        logger.warning("CSV not found: %s", csv_path)
        return ratings

    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            sample = f.read(1024)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample)
            reader = csv.DictReader(f, dialect=dialect)
            for row in reader:
                clean = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                home = clean.get("home") or clean.get("hometeam", "")
                away = clean.get("away") or clean.get("awayteam", "")
                hg_raw = clean.get("home_goals") or clean.get("fthg", "")
                ag_raw = clean.get("away_goals") or clean.get("ftag", "")
                if not home or not away or hg_raw == "" or ag_raw == "":
                    continue
                try:
                    home_goals = int(float(hg_raw))
                    away_goals = int(float(ag_raw))
                except (ValueError, TypeError):
                    continue
                update_elo(ratings, home, away, home_goals, away_goals)
    except Exception as exc:
        logger.warning("Error processing CSV %s: %s", csv_path, exc)

    return ratings


def get_team_elo(team_name: str) -> float:
    """Get a single team's Elo rating (loads from file)."""
    ratings = load_elo_ratings()
    return ratings.get(team_name, DEFAULT_ELO)
