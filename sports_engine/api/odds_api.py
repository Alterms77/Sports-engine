"""
The Odds API v4 client — https://the-odds-api.com

Fetches live bookmaker odds for all major sports and returns them as
``MarketScan`` objects ready to be consumed by the auto-scanner engine.

Supported markets (when ODDS_API_KEY is set):
  h2h      — match winner (1X2 / moneyline)
  totals   — Over/Under points / goals / runs
  spreads  — point spread / handicap

Rate-limiting notes
───────────────────
The free tier provides 500 requests/month.  Every call to ``get_odds()``
consumes **one** request from the quota.  The remaining quota is read from
the ``x-requests-remaining`` response header and stored in-module so callers
can back off when it gets low.

Caching
───────
Responses are cached in memory for ``CACHE_TTL`` seconds (default 5 min) to
avoid burning quota on repeated calls within the same scan cycle.

Fallback
────────
When no API key is configured the module returns an empty list rather than
raising an exception so the auto-scanner can continue using ESPN events.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import requests

from core.market_scanner import BookmakerOdds, MarketScan

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_TTL = 300  # seconds (5 min)
_CACHE: dict = {}          # url+params → (data, timestamp)
_quota_remaining: int = 500  # updated from response header

# Note: the Tennis ATP entry points to the current major tournament.
# Update the value in AUTO_SPORTS when the season changes (e.g. US Open →
# "tennis_atp_us_open").  The key itself ("Tennis ATP") stays constant.



# ── Sports to scan automatically ─────────────────────────────────────────────
# Maps internal sport key → The Odds API sport_key value.
# Add or remove entries to control which sports are auto-scanned.
AUTO_SPORTS: dict = {
    "Soccer EPL":     "soccer_epl",
    "Soccer La Liga": "soccer_spain_la_liga",
    "Soccer Liga MX": "soccer_mexico_ligamx",
    "Soccer UCL":     "soccer_uefa_champs_league",
    "NBA":            "basketball_nba",
    "NFL":            "americanfootball_nfl",
    "MLB":            "baseball_mlb",
    "NHL":            "icehockey_nhl",
    "Tennis ATP":     "tennis_atp_french_open",  # replaced each tournament
}

# Bookmaker regions to query (us = mostly US books; eu adds European books)
DEFAULT_REGIONS = "us,eu"
# Markets to request in a single call (saves quota vs multiple calls)
DEFAULT_MARKETS = "h2h,totals,spreads"
# Odds format
ODDS_FORMAT = "decimal"


def _fetch(url: str, params: dict) -> Optional[dict]:
    """Cached GET to The Odds API.  Returns parsed JSON or None on error."""
    global _quota_remaining

    cache_key = url + str(sorted(params.items()))
    now = time.time()
    if cache_key in _CACHE:
        data, ts = _CACHE[cache_key]
        if now - ts < CACHE_TTL:
            return data

    try:
        resp = requests.get(url, params=params, timeout=12)
        # Update quota tracker
        remaining = resp.headers.get("x-requests-remaining")
        if remaining is not None:
            try:
                _quota_remaining = int(remaining)
            except ValueError:
                pass

        if resp.status_code == 401:
            logger.warning("Odds API: invalid API key (401)")
            return None
        if resp.status_code == 429:
            logger.warning("Odds API: quota exceeded (429)")
            return None
        if not resp.ok:
            logger.warning("Odds API HTTP %s: %s", resp.status_code, url)
            return None

        data = resp.json()
        _CACHE[cache_key] = (data, now)
        logger.debug("Odds API OK: %s (quota left: %s)", url, _quota_remaining)
        return data

    except requests.exceptions.Timeout:
        logger.warning("Odds API timeout: %s", url)
    except requests.exceptions.RequestException as exc:
        logger.warning("Odds API error [%s]: %s", url, exc)
    return None


def get_quota_remaining() -> int:
    """Return the last known remaining request quota (best-effort)."""
    return _quota_remaining


def get_sports(api_key: str) -> List[str]:
    """Return list of active sport keys from The Odds API."""
    from core.config import ODDS_API_BASE_URL
    data = _fetch(f"{ODDS_API_BASE_URL}/sports", {"apiKey": api_key, "all": "false"})
    if not data:
        return []
    return [s["key"] for s in data if not s.get("has_outrights", False)]


def get_odds(
    sport_key: str,
    api_key: str,
    regions: str = DEFAULT_REGIONS,
    markets: str = DEFAULT_MARKETS,
) -> List[MarketScan]:
    """
    Fetch bookmaker odds for all upcoming events in ``sport_key`` and convert
    them to a flat list of ``MarketScan`` objects.

    Parameters
    ----------
    sport_key : The Odds API sport key (e.g. "basketball_nba")
    api_key   : Your The Odds API key
    regions   : comma-separated region codes (default "us,eu")
    markets   : comma-separated market codes (default "h2h,totals,spreads")

    Returns
    -------
    List of ``MarketScan`` ready for ``scan_multiple_markets()``.
    """
    from core.config import ODDS_API_BASE_URL

    if not api_key:
        return []

    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": "iso",
    }
    data = _fetch(f"{ODDS_API_BASE_URL}/sports/{sport_key}/odds", params)
    if not data:
        return []

    scans: List[MarketScan] = []
    sport_label = _sport_label(sport_key)

    for event in data:
        event_name = f"{event.get('home_team', '?')} vs {event.get('away_team', '?')}"
        home_team = event.get("home_team", "Home")
        away_team = event.get("away_team", "Away")

        # Collect all bookmaker odds for each market+outcome
        # Structure: {(market_key, outcome_label): [BookmakerOdds, ...]}
        market_odds: dict = {}

        for bookmaker in event.get("bookmakers", []):
            bk_name = bookmaker.get("title", bookmaker.get("key", "Unknown"))
            for market in bookmaker.get("markets", []):
                mkey = market.get("key", "")
                for outcome in market.get("outcomes", []):
                    label = _outcome_label(mkey, outcome, home_team, away_team)
                    if not label:
                        continue
                    slot_key = (mkey, label)
                    market_odds.setdefault(slot_key, [])
                    try:
                        price = float(outcome.get("price", 0))
                        if price > 1.0:
                            market_odds[slot_key].append(
                                BookmakerOdds(bookmaker=bk_name, odds=price)
                            )
                    except (TypeError, ValueError):
                        pass

        # Convert each (market, outcome) bucket to a MarketScan
        for (mkey, label), odds_list in market_odds.items():
            if len(odds_list) < 2:  # need at least 2 bookmakers to detect discrepancy
                continue
            scans.append(MarketScan(
                sport=sport_label,
                event=event_name,
                market=label,
                odds_list=odds_list,
            ))

    logger.debug("Odds API: %d MarketScan objects from %s (%d events)",
                 len(scans), sport_key, len(data))
    return scans


def get_all_odds(api_key: str) -> List[MarketScan]:
    """
    Fetch odds for all sports in ``AUTO_SPORTS`` and return a combined list
    of ``MarketScan`` objects.  Skips sports whose API calls fail gracefully.
    """
    if not api_key:
        logger.debug("ODDS_API_KEY not set — skipping live odds fetch")
        return []

    all_scans: List[MarketScan] = []
    for sport_label, sport_key in AUTO_SPORTS.items():
        try:
            scans = get_odds(sport_key, api_key)
            all_scans.extend(scans)
            logger.debug("Odds API: %s → %d scans", sport_label, len(scans))
        except Exception as exc:
            logger.warning("Odds API: error fetching %s: %s", sport_label, exc)

    logger.info("Odds API: fetched %d total MarketScan objects across all sports",
                len(all_scans))
    return all_scans


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sport_label(sport_key: str) -> str:
    """Return a human-readable sport label from an API sport key."""
    reverse = {v: k for k, v in AUTO_SPORTS.items()}
    if sport_key in reverse:
        return reverse[sport_key]
    # Fallback: clean up the key
    return sport_key.replace("_", " ").title()


def _outcome_label(
    market_key: str,
    outcome: dict,
    home_team: str,
    away_team: str,
) -> str:
    """
    Build a human-readable market label from an Odds API outcome dict.

    Examples
    --------
    h2h   + name="Lakers"          → "Victoria Lakers"
    h2h   + name="Draw"            → "Empate"
    totals + name="Over" + pt=220  → "Over 220.0"
    totals + name="Under" + pt=220 → "Under 220.0"
    spreads + name="Lakers" + pt=-4.5 → "Lakers -4.5"
    """
    name = outcome.get("name", "")
    point = outcome.get("point")

    if market_key == "h2h":
        if name.lower() in ("draw", "tie", "empate"):
            return "Empate"
        return f"Victoria {name}"

    if market_key == "totals":
        if point is not None:
            return f"{name} {point}"
        return name

    if market_key == "spreads":
        if point is not None:
            sign = "+" if point > 0 else ""
            return f"{name} {sign}{point}"
        return f"{name} Spread"

    return f"{name} ({market_key})"


def clear_cache() -> None:
    """Manually invalidate the in-memory cache."""
    _CACHE.clear()
