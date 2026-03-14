"""
Central configuration module for Sports Engine.
All secrets and environment variables are loaded here.
"""

import os
import logging

logger = logging.getLogger(__name__)

# ===============================
# 🔑 SECRETS (from environment only — no fallbacks)
# ===============================

TELEGRAM_TOKEN: str = os.getenv("TOKEN", "")
API_SPORTS_KEY: str = os.getenv("API_SPORTS_KEY", "")

# ===============================
# 🌐 API ENDPOINTS
# ===============================

API_SPORTS_BASE_URL = "https://v3.football.api-sports.io"

# ===============================
# ⚽ LEAGUE IDs
# ===============================

LEAGUE_IDS = {
    "Liga MX": 262,
    "Premier League": 39,
    "La Liga": 140,
    "Champions League": 2,
    "Serie A": 135,
    "Bundesliga": 78,
}

ALLOWED_LEAGUE_IDS = {
    262: "Liga MX",
    39: "Premier League",
    140: "La Liga",
    2: "Champions League",
}

# ===============================
# 🏟️ LEAGUE-SPECIFIC HOME ADVANTAGE
# ===============================

HOME_ADVANTAGE = {
    "Liga MX": 1.15,
    "Premier League": 1.08,
    "La Liga": 1.10,
    "Champions League": 1.05,
    "Serie A": 1.10,
    "Bundesliga": 1.09,
    "default": 1.10,
}

# ===============================
# 🎲 MONTE CARLO SIMULATIONS
# ===============================

MONTE_CARLO_SIMULATIONS = 50_000


def validate_config() -> bool:
    """Validate that required environment variables are set."""
    ok = True
    if not TELEGRAM_TOKEN:
        logger.error("TOKEN environment variable is not set")
        ok = False
    if not API_SPORTS_KEY:
        logger.warning("API_SPORTS_KEY environment variable is not set – live match updates disabled")
    return ok
