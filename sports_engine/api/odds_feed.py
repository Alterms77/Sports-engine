"""
Odds Feed Manager — data layer for the Universal Market Error Scanner.

Stores tracked markets in Postgres when DATABASE_URL is configured (Railway),
falling back to data/tracked_markets.json for local / CSV-only deployments.

Each tracked market looks like:
{
    "sport":   "NBA",
    "event":   "Lakers vs Warriors",
    "market":  "LeBron Puntos +25.5",
    "player":  "LeBron James",          // optional
    "odds": [
        {"bookmaker": "Bet365",   "odds": 1.85},
        {"bookmaker": "Caliente", "odds": 2.20},
        {"bookmaker": "Codere",   "odds": 1.90}
    ],
    "added_at":   "2026-03-15T08:00:00",
    "updated_at": "2026-03-15T08:00:00"
}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import List

from core.market_scanner import BookmakerOdds, MarketScan

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# FILE PATH (fallback when Postgres is unavailable)
# ──────────────────────────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)
TRACKED_MARKETS_FILE = os.path.join(_DATA_DIR, "tracked_markets.json")


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS — detect DB availability once per process
# ──────────────────────────────────────────────────────────────────────────────

def _db_available() -> bool:
    try:
        from core.db import is_available
        return is_available()
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _make_market_key(m: dict) -> str:
    """Return a normalised key ``event|market|player`` for deduplication."""
    return (
        f"{m.get('event','').lower()}|"
        f"{m.get('market','').lower()}|"
        f"{m.get('player','').lower()}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# RAW JSON I/O (local fallback)
# ──────────────────────────────────────────────────────────────────────────────

def _load_raw_json() -> List[dict]:
    """Load raw market list from JSON.  Returns [] on any error."""
    if not os.path.exists(TRACKED_MARKETS_FILE):
        return []
    try:
        with open(TRACKED_MARKETS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not load tracked_markets.json: %s", exc)
        return []


def _save_raw_json(data: List[dict]) -> None:
    """Persist market list to JSON (local fallback)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    try:
        with open(TRACKED_MARKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Could not save tracked_markets.json: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# UNIFIED RAW I/O — Postgres preferred, JSON fallback
# ──────────────────────────────────────────────────────────────────────────────

def _load_raw() -> List[dict]:
    """Return raw market list, preferring Postgres when DATABASE_URL is set."""
    if _db_available():
        try:
            from core.db import get_tracked_markets_raw
            return get_tracked_markets_raw()
        except Exception as exc:
            logger.warning("odds_feed: DB load failed, falling back to JSON — %s", exc)
    return _load_raw_json()


def _save_raw(data: List[dict]) -> None:
    """Persist market list, preferring Postgres when DATABASE_URL is set.

    When Postgres is available the full list is reconciled (upsert new/updated,
    delete removed).  The JSON file is only written in local / no-DB mode.
    """
    if _db_available():
        try:
            from core.db import (
                get_tracked_markets_raw,
                save_tracked_market,
                remove_tracked_market,
            )
            new_keys = {_make_market_key(m) for m in data}
            # Remove entries that are no longer in the list
            for old in get_tracked_markets_raw():
                if _make_market_key(old) not in new_keys:
                    remove_tracked_market(old["event"], old["market"], old.get("player", ""))
            # Upsert all current entries
            for m in data:
                save_tracked_market(
                    sport=m.get("sport", ""),
                    event=m.get("event", ""),
                    market=m.get("market", ""),
                    player=m.get("player", ""),
                    odds=m.get("odds", []),
                )
            return
        except Exception as exc:
            logger.warning("odds_feed: DB save failed, falling back to JSON — %s", exc)
    _save_raw_json(data)


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def get_tracked_markets() -> List[MarketScan]:
    """
    Load all tracked markets and return them as MarketScan objects.
    Entries with fewer than 2 valid odds entries are skipped.
    """
    raw = _load_raw()
    scans: List[MarketScan] = []

    for m in raw:
        try:
            odds_list = [
                BookmakerOdds(bookmaker=e["bookmaker"], odds=float(e["odds"]))
                for e in m.get("odds", [])
                if float(e.get("odds", 0)) > 1.0
            ]
            if len(odds_list) < 2:
                continue
            scans.append(MarketScan(
                sport     = m.get("sport",  "General"),
                event     = m.get("event",  ""),
                market    = m.get("market", ""),
                player    = m.get("player", ""),
                odds_list = odds_list,
            ))
        except Exception as exc:
            logger.debug("Skipping malformed tracked market: %s", exc)

    return scans


def add_market(
    sport:      str,
    event:      str,
    market:     str,
    odds_list:  List[BookmakerOdds],
    player:     str = "",
) -> None:
    """
    Add or update a tracked market.

    If an entry with the same (event, market, player) already exists,
    its odds are updated in place.  Otherwise a new entry is appended.
    """
    raw  = _load_raw()
    now  = datetime.utcnow().isoformat(timespec="seconds")
    key  = f"{event.lower()}|{market.lower()}|{player.lower()}"

    for entry in raw:
        if _make_market_key(entry) == key:
            entry["odds"]       = [{"bookmaker": b.bookmaker, "odds": b.odds} for b in odds_list]
            entry["sport"]      = sport
            entry["updated_at"] = now
            _save_raw(raw)
            return

    raw.append({
        "sport":      sport,
        "event":      event,
        "market":     market,
        "player":     player,
        "odds":       [{"bookmaker": b.bookmaker, "odds": b.odds} for b in odds_list],
        "added_at":   now,
        "updated_at": now,
    })
    _save_raw(raw)


def remove_market(event: str, market: str, player: str = "") -> bool:
    """
    Remove a specific tracked market.  Returns True if found and removed.
    """
    raw = _load_raw()
    key = f"{event.lower()}|{market.lower()}|{player.lower()}"
    new = [m for m in raw if _make_market_key(m) != key]
    if len(new) < len(raw):
        _save_raw(new)
        return True
    return False


def clear_markets() -> int:
    """Remove ALL tracked markets.  Returns the count that was removed."""
    if _db_available():
        try:
            from core.db import clear_tracked_markets
            n = clear_tracked_markets()
            if n >= 0:
                return n
        except Exception as exc:
            logger.warning("odds_feed: DB clear failed, falling back to JSON — %s", exc)
    raw   = _load_raw_json()
    count = len(raw)
    _save_raw_json([])
    return count


def market_count() -> int:
    """Return the number of currently tracked markets."""
    return len(_load_raw())


def list_markets_text() -> str:
    """Return a formatted list of tracked markets for Telegram display."""
    raw = _load_raw()
    if not raw:
        return "📭 No hay mercados en seguimiento.\nUsa `/scanodds` para agregar uno."

    lines = [f"📋 *Mercados en seguimiento:* `{len(raw)}`\n"]
    for i, m in enumerate(raw, 1):
        player_part = f" ({m['player']})" if m.get("player") else ""
        n_sources   = len(m.get("odds", []))
        lines.append(
            f"  {i}. `{m.get('sport','?')}` — {m.get('event','?')}\n"
            f"     📊 {m.get('market','?')}{player_part}  "
            f"_{n_sources} casas_"
        )
    return "\n".join(lines)
