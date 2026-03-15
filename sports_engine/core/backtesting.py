"""
Pick tracking and backtesting for Sports Engine.

Two modes:
1. LIVE TRACKING: Every /predict command logs the pick to picks_log.json
   with timestamp, predicted result, confidence, and later the actual result.

2. HISTORICAL BACKTEST: Run against CSV match data to measure model accuracy.

The /stats command (bot prediction stats) reads picks_log.json and shows:
  - Total picks, correct picks, hit rate %
  - ROI % (if odds were provided)
  - Breakdown by confidence level (ALTA/MEDIA/BAJA hit rates)
  - Last 7 days performance
"""

import json
import os
import csv
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PICKS_LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "picks_log.json",
)


def _load_picks() -> List[dict]:
    """Load all picks from the log file."""
    if not os.path.exists(PICKS_LOG_FILE):
        return []
    try:
        with open(PICKS_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.warning("Could not load picks log: %s", exc)
    return []


def _save_picks(picks: List[dict]) -> None:
    """Persist picks list to file."""
    os.makedirs(os.path.dirname(PICKS_LOG_FILE), exist_ok=True)
    try:
        with open(PICKS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(picks, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Could not save picks log: %s", exc)


def log_pick(prediction: dict) -> None:
    """
    Log a prediction pick for tracking.
    Stores: timestamp, home, away, predicted_winner, confidence,
    home_win%, draw%, away_win%, xg_home, xg_away
    """
    picks = _load_picks()

    # Determine predicted winner
    probs = {
        "HOME": prediction.get("home_win", 0),
        "DRAW": prediction.get("draw", 0),
        "AWAY": prediction.get("away_win", 0),
    }
    predicted_winner = max(probs, key=probs.get)

    pick = {
        "id": len(picks) + 1,
        "timestamp": datetime.utcnow().isoformat(),
        "home": prediction.get("home", ""),
        "away": prediction.get("away", ""),
        "league": prediction.get("league", "default"),
        "predicted_winner": predicted_winner,
        "confidence": prediction.get("confidence", "BAJA"),
        "home_win": prediction.get("home_win", 0),
        "draw": prediction.get("draw", 0),
        "away_win": prediction.get("away_win", 0),
        "xg_home": prediction.get("xg_home", 0),
        "xg_away": prediction.get("xg_away", 0),
        "actual_result": None,  # filled in later by update_results_from_api
        "correct": None,
    }
    picks.append(pick)
    _save_picks(picks)


def get_stats_summary(days: int = 30) -> dict:
    """
    Return stats summary for the bot /stats command.

    Returns:
    {
        "total_picks": int,
        "correct": int,
        "hit_rate": float,  # percentage
        "by_confidence": {
            "ALTA": {"total": int, "correct": int, "hit_rate": float},
            "MEDIA": {"total": int, "correct": int, "hit_rate": float},
            "BAJA": {"total": int, "correct": int, "hit_rate": float},
        },
        "last_7_days": {"total": int, "correct": int, "hit_rate": float},
        "streak": int,  # current correct streak
    }
    """
    picks = _load_picks()

    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_7d = datetime.utcnow() - timedelta(days=7)

    recent = []
    for p in picks:
        try:
            ts = datetime.fromisoformat(p["timestamp"])
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            recent.append(p)

    total = len(recent)
    correct_count = sum(1 for p in recent if p.get("correct") is True)

    hit_rate = round(correct_count / total * 100, 1) if total > 0 else 0.0

    by_confidence: Dict[str, dict] = {}
    for level in ["ALTA", "MEDIA", "BAJA"]:
        level_picks = [p for p in recent if p.get("confidence") == level]
        level_correct = sum(1 for p in level_picks if p.get("correct") is True)
        level_total = len(level_picks)
        by_confidence[level] = {
            "total": level_total,
            "correct": level_correct,
            "hit_rate": round(level_correct / level_total * 100, 1) if level_total > 0 else 0.0,
        }

    # Last 7 days
    last7_picks = [
        p for p in recent
        if _parse_ts(p.get("timestamp", "")) >= cutoff_7d
    ]
    last7_total = len(last7_picks)
    last7_correct = sum(1 for p in last7_picks if p.get("correct") is True)
    last_7_days = {
        "total": last7_total,
        "correct": last7_correct,
        "hit_rate": round(last7_correct / last7_total * 100, 1) if last7_total > 0 else 0.0,
    }

    # Current correct streak (most recent picks first)
    streak = 0
    for p in reversed(recent):
        if p.get("correct") is True:
            streak += 1
        else:
            break

    return {
        "total_picks": total,
        "correct": correct_count,
        "hit_rate": hit_rate,
        "by_confidence": by_confidence,
        "last_7_days": last_7_days,
        "streak": streak,
    }


def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO timestamp, return epoch on failure."""
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        logger.warning("Could not parse timestamp: %r", ts_str)
        return datetime(1970, 1, 1)


def update_results_from_api() -> int:
    """
    Check finished matches against logged picks and mark correct/incorrect.
    Returns number of picks updated.
    Uses TheSportsDB or ESPN to get final scores.
    """
    picks = _load_picks()
    updated = 0

    unresolved = [p for p in picks if p.get("actual_result") is None]
    if not unresolved:
        return 0

    try:
        from api.live_aggregator import get_today_schedule
        events = get_today_schedule("football")
        finished = {
            f"{e.get('home_team', '')}|{e.get('away_team', '')}": e
            for e in (events or [])
            if e.get("status") in ("FT", "finished", "Final", "AET", "Pen")
        }
    except Exception as exc:
        logger.debug("Could not fetch results for backtesting: %s", exc)
        return 0

    for pick in unresolved:
        key = f"{pick['home']}|{pick['away']}"
        event = finished.get(key)
        if not event:
            continue
        try:
            hg = int(event.get("home_score", -1))
            ag = int(event.get("away_score", -1))
        except (TypeError, ValueError):
            continue
        if hg < 0 or ag < 0:
            continue

        if hg > ag:
            actual = "HOME"
        elif hg < ag:
            actual = "AWAY"
        else:
            actual = "DRAW"

        pick["actual_result"] = actual
        pick["correct"] = pick["predicted_winner"] == actual
        updated += 1

    if updated:
        _save_picks(picks)

    return updated


def backtest_from_csv(csv_path: str) -> dict:
    """
    Run the current prediction model against historical CSV data.
    Returns accuracy metrics.
    """
    if not os.path.exists(csv_path):
        return {"error": f"CSV not found: {csv_path}"}

    try:
        from sports.football import predict_match
    except ImportError as exc:
        return {"error": f"Could not import predict_match: {exc}"}

    matches = []
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            sample = f.read(1024)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample)
            reader = csv.DictReader(f, dialect=dialect)
            for row in reader:
                clean = {k.strip().lower(): v.strip() for k, v in row.items() if k}
                matches.append(clean)
    except Exception as exc:
        return {"error": f"Could not read CSV: {exc}"}

    correct = total = 0
    by_confidence: Dict[str, dict] = {"ALTA": {"c": 0, "t": 0}, "MEDIA": {"c": 0, "t": 0}, "BAJA": {"c": 0, "t": 0}}

    for m in matches:
        home = m.get("home") or m.get("hometeam", "")
        away = m.get("away") or m.get("awayteam", "")
        hg_raw = m.get("home_goals") or m.get("fthg", "")
        ag_raw = m.get("away_goals") or m.get("ftag", "")
        if not home or not away or hg_raw == "" or ag_raw == "":
            continue
        try:
            hg = int(float(hg_raw))
            ag = int(float(ag_raw))
        except (ValueError, TypeError):
            continue

        if hg > ag:
            actual = "HOME"
        elif hg < ag:
            actual = "AWAY"
        else:
            actual = "DRAW"

        try:
            pred = predict_match(home, away)
        except Exception:
            continue

        probs = {"HOME": pred["home_win"], "DRAW": pred["draw"], "AWAY": pred["away_win"]}
        predicted = max(probs, key=probs.get)
        conf = pred.get("confidence", "BAJA")

        is_correct = predicted == actual
        if is_correct:
            correct += 1
        total += 1
        level = conf if conf in by_confidence else "BAJA"
        by_confidence[level]["t"] += 1
        if is_correct:
            by_confidence[level]["c"] += 1

    accuracy = round(correct / total * 100, 1) if total > 0 else 0.0
    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "by_confidence": {
            k: {
                "total": v["t"],
                "correct": v["c"],
                "hit_rate": round(v["c"] / v["t"] * 100, 1) if v["t"] > 0 else 0.0,
            }
            for k, v in by_confidence.items()
        },
    }
