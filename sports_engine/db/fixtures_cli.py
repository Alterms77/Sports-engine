#!/usr/bin/env python
"""
fixtures_cli.py — Debug helper for the fixtures DB.

Usage (from repo root):
    python sports_engine/db/fixtures_cli.py

Requires DATABASE_URL to be set in the environment (or a .env file).
"""

import os
import sys

# Ensure sports_engine/ is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_SPORTS_ENGINE = os.path.dirname(_HERE)
for _p in (_SPORTS_ENGINE, os.path.dirname(_SPORTS_ENGINE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.db import (
    is_available,
    ensure_table,
    get_upcoming_count,
    get_upcoming_fixtures,
    get_latest_finished,
)


def _fmt_row(row: dict) -> str:
    kickoff = row.get("kickoff_utc", "")
    if hasattr(kickoff, "strftime"):
        kickoff = kickoff.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"  [{row.get('status_short','?'):4s}] "
        f"{row.get('home_team','?'):25s} vs {row.get('away_team','?'):25s} "
        f"| {row.get('league_name',''):30s} | {kickoff}"
    )


def main() -> None:
    print("Sports-Engine Fixtures CLI")
    print("=" * 70)

    if not is_available():
        print(
            "ERROR: DATABASE_URL not set or psycopg not installed.\n"
            "  - Set DATABASE_URL in your environment or .env file.\n"
            "  - Install driver: pip install 'psycopg[binary]>=3.1'"
        )
        sys.exit(1)

    print("Database: connected ✓")
    ensure_table()

    # Upcoming count
    count = get_upcoming_count()
    print(f"\nUpcoming not-started fixtures: {count}")

    # Next 24h upcoming
    upcoming = get_upcoming_fixtures(hours=24)
    if upcoming:
        print(f"\nNext 24 hours ({len(upcoming)} fixture(s)):")
        for row in upcoming:
            print(_fmt_row(row))
    else:
        print("\nNo upcoming fixtures in the next 24 hours.")

    # Latest finished
    finished = get_latest_finished(limit=10)
    if finished:
        print(f"\nLatest finished fixtures (up to 10):")
        for row in finished:
            print(_fmt_row(row))
    else:
        print("\nNo finished fixtures recorded yet.")


if __name__ == "__main__":
    main()
