"""
Central configuration module for Sports Engine.
All secrets and environment variables are loaded here.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Load .env file into os.environ if it exists (local development / tests).
# This is intentionally called here — at the top of the config module —
# so that every entry point (bot.py, update_matches.py, scripts, tests)
# picks up the .env file regardless of whether load_dotenv() was called
# earlier. python-dotenv's default behaviour does NOT override values that
# are already set in the real environment, so Railway / Docker env vars are
# always respected.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on real environment variables

# ===============================
# 🔑 SECRETS (loaded from environment; empty string signals "not set"
#             and is checked / rejected by validate_config())
# ===============================

TELEGRAM_TOKEN: str = os.getenv("TOKEN", "")
API_SPORTS_KEY: str = os.getenv("API_SPORTS_KEY", "")
ALERTS_CHANNEL_ID: str = os.getenv("ALERTS_CHANNEL_ID", "")

# ── The Odds API (https://the-odds-api.com) ───────────────────────────────────
# Optional: if set, the auto-scanner fetches live bookmaker odds automatically.
# Without this key the scanner still runs, using ESPN events + model-based odds.
ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"

# ── Sportradar (https://developer.sportradar.com) ─────────────────────────────
# Provides richer stats than ESPN: NBA OffRtg/DefRtg/pace, MLB starting pitcher
# ERA/WHIP, NFL scoring efficiency, etc.  When set, sport predictors prefer
# Sportradar data over ESPN season averages.
# SPORTRADAR_ACCESS: "trial" (default) for trial accounts; "" for production.
SPORTRADAR_API_KEY: str = os.getenv("SPORTRADAR_API_KEY", "")
SPORTRADAR_ACCESS: str = os.getenv("SPORTRADAR_ACCESS", "trial")

# ── Auto-scanner tuning ────────────────────────────────────────────────────────
# Interval (seconds) between full auto-scan cycles. Default 300 s (5 min).
# With the free Odds-API tier (500 req/month) a longer interval is recommended.
AUTO_SCAN_INTERVAL: int = int(os.getenv("AUTO_SCAN_INTERVAL", "300"))
# Minimum EV% before an alert is sent (avoids marginal-value spam).
AUTO_SCAN_MIN_EV: float = float(os.getenv("AUTO_SCAN_MIN_EV", "5.0"))
# How long (seconds) to suppress a repeated identical alert (deduplication TTL).
AUTO_SCAN_DEDUP_TTL: int = int(os.getenv("AUTO_SCAN_DEDUP_TTL", "3600"))

# ===============================
# 🌐 API ENDPOINTS
# ===============================

API_SPORTS_BASE_URL = "https://v3.football.api-sports.io"

# ── ESPN (free public API, no key needed) ───────────────────────────────────
ESPN_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_CACHE_TTL = 1800  # seconds (30 min)

# ── SofaScore (unofficial public REST API, no key needed) ────────────────────
SOFASCORE_BASE_URL = "https://api.sofascore.com/api/v1"
SOFASCORE_CACHE_TTL = 900   # 15 min (live data changes fast)
SOFASCORE_LIVE_TTL  = 60    # 1 min for live events

# SofaScore unique-tournament IDs + season IDs (2024-25 season)
SOFASCORE_LEAGUES = {
    "Premier League": {"tournament_id": 17,  "season_id": 61627},
    "La Liga":        {"tournament_id": 8,   "season_id": 61643},
    "Bundesliga":     {"tournament_id": 35,  "season_id": 63516},
    "Serie A":        {"tournament_id": 23,  "season_id": 63515},
    "Ligue 1":        {"tournament_id": 34,  "season_id": 63520},
    "Liga MX":        {"tournament_id": 352, "season_id": 63698},
    "Champions League": {"tournament_id": 7, "season_id": 61644},
}

# ── TheSportsDB (completely free, public API, key "3") ────────────────────────
THESPORTSDB_BASE_URL = "https://www.thesportsdb.com/api/v1/json/3"
THESPORTSDB_CACHE_TTL = 1800  # 30 min

# TheSportsDB league IDs
THESPORTSDB_LEAGUES = {
    "Premier League":   "4328",
    "La Liga":          "4335",
    "Bundesliga":       "4331",
    "Serie A":          "4332",
    "Ligue 1":          "4334",
    "Liga MX":          "4350",
    "MLS":              "4346",
    "Champions League": "4480",
    "NBA":              "4387",
    "NFL":              "4391",
    "MLB":              "4424",
    "NHL":              "4380",
}

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
    135: "Serie A",
    78: "Bundesliga",
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


# ===============================
# 🗺️ DATASET TEAM → LEAGUE MAPPING
# Maps exact dataset names (as in matches.csv) to league names.
# Used for league-specific home advantage and display context.
# ===============================

DATASET_TEAM_LEAGUES: dict = {
    # ── Liga MX ──
    "Club America": "Liga MX",
    "Guadalajara Chivas": "Liga MX",
    "Cruz Azul": "Liga MX",
    "Tigres UANL": "Liga MX",
    "Monterrey": "Liga MX",
    "Santos Laguna": "Liga MX",
    "Pachuca": "Liga MX",
    "UNAM Pumas": "Liga MX",
    "Atlas": "Liga MX",
    "Toluca": "Liga MX",
    "Queretaro": "Liga MX",
    "Necaxa": "Liga MX",
    "Club Tijuana": "Liga MX",
    "Mazatlan FC": "Liga MX",
    "Club Leon": "Liga MX",
    "Juarez": "Liga MX",
    "Atl. San Luis": "Liga MX",
    "Chiapas": "Liga MX",
    "Atlante": "Liga MX",
    "Veracruz": "Liga MX",
    "Dorados de Sinaloa": "Liga MX",
    "Leones Negros": "Liga MX",
    "Lobos BUAP": "Liga MX",
    "Monarcas": "Liga MX",
    # ── Premier League ──
    "Liverpool": "Premier League",
    "Bournemouth": "Premier League",
    "Aston Villa": "Premier League",
    "Newcastle": "Premier League",
    "Brighton": "Premier League",
    "Fulham": "Premier League",
    "Sunderland": "Premier League",
    "West Ham": "Premier League",
    "Arsenal": "Premier League",
    "Chelsea": "Premier League",
    "Man City": "Premier League",
    "Man United": "Premier League",
    "Tottenham": "Premier League",
    "Wolves": "Premier League",
    "Everton": "Premier League",
    "Brentford": "Premier League",
    "Crystal Palace": "Premier League",
    "Nott'm Forest": "Premier League",
    "Leeds": "Premier League",
    "Burnley": "Premier League",
    "Sheffield United": "Premier League",
    "Luton": "Premier League",
    # ── La Liga ──
    "Real Madrid": "La Liga",
    "Barcelona": "La Liga",
    "Ath Madrid": "La Liga",
    "Sevilla": "La Liga",
    "Valencia": "La Liga",
    "Betis": "La Liga",
    "Villarreal": "La Liga",
    "Sociedad": "La Liga",
    "Ath Bilbao": "La Liga",
    "Girona": "La Liga",
    "Alaves": "La Liga",
    "Mallorca": "La Liga",
    "Getafe": "La Liga",
    "Osasuna": "La Liga",
    "Vallecano": "La Liga",
    "Celta": "La Liga",
    "Espanol": "La Liga",
    "Oviedo": "La Liga",
    "Elche": "La Liga",
    "Levante": "La Liga",
    # ── Serie A ──
    "Inter": "Serie A",
    "Milan": "Serie A",
    "Juventus": "Serie A",
    "Napoli": "Serie A",
    "Roma": "Serie A",
    "Lazio": "Serie A",
    "Atalanta": "Serie A",
    "Fiorentina": "Serie A",
    "Bologna": "Serie A",
    "Torino": "Serie A",
    "Como": "Serie A",
    "Cremonese": "Serie A",
    "Genoa": "Serie A",
    "Udinese": "Serie A",
    "Sassuolo": "Serie A",
    "Lecce": "Serie A",
    "Cagliari": "Serie A",
    "Empoli": "Serie A",
    "Verona": "Serie A",
    "Parma": "Serie A",
    "Pisa": "Serie A",
    "Monza": "Serie A",
    "Salernitana": "Serie A",
    # ── Bundesliga ──
    "Bayern Munich": "Bundesliga",
    "RB Leipzig": "Bundesliga",
    "Ein Frankfurt": "Bundesliga",
    "Werder Bremen": "Bundesliga",
    "Freiburg": "Bundesliga",
    "Augsburg": "Bundesliga",
    "Heidenheim": "Bundesliga",
    "Wolfsburg": "Bundesliga",
    "Leverkusen": "Bundesliga",
    "Hoffenheim": "Bundesliga",
    "Union Berlin": "Bundesliga",
    "Stuttgart": "Bundesliga",
    "St Pauli": "Bundesliga",
    "Dortmund": "Bundesliga",
    "Mainz": "Bundesliga",
    "FC Koln": "Bundesliga",
    "M'gladbach": "Bundesliga",
    "Hamburg": "Bundesliga",
}


def detect_league(home_team: str, away_team: str) -> str:
    """
    Auto-detect the league from the resolved team names.
    Returns the league name or 'default' if unknown.
    """
    league_home = DATASET_TEAM_LEAGUES.get(home_team)
    league_away = DATASET_TEAM_LEAGUES.get(away_team)
    if league_home and league_home == league_away:
        return league_home
    if league_home:
        return league_home
    if league_away:
        return league_away
    return "default"


def validate_config() -> bool:
    """Validate that required environment variables are set and log API key status."""
    ok = True
    if not TELEGRAM_TOKEN:
        logger.error("TOKEN environment variable is not set")
        ok = False

    # Report optional API key status so Railway logs make it clear what's available
    if API_SPORTS_KEY:
        logger.info("API_SPORTS_KEY: configured ✓ (live soccer fixtures enabled)")
    else:
        logger.warning(
            "API_SPORTS_KEY: NOT set — live soccer match updates disabled. "
            "Set this in Railway to get today's real fixtures."
        )

    if SPORTRADAR_API_KEY:
        logger.info(
            "SPORTRADAR_API_KEY: configured ✓ (access level: %s)",
            SPORTRADAR_ACCESS or "production",
        )
    else:
        logger.info(
            "SPORTRADAR_API_KEY: not set (optional — ESPN stats used instead)"
        )

    if ODDS_API_KEY:
        logger.info("ODDS_API_KEY: configured ✓ (auto-scanner will use live odds)")
    else:
        logger.info(
            "ODDS_API_KEY: not set (optional — auto-scanner uses model-based odds)"
        )

    if ALERTS_CHANNEL_ID:
        logger.info("ALERTS_CHANNEL_ID: configured ✓ (%s)", ALERTS_CHANNEL_ID)
    else:
        logger.info(
            "ALERTS_CHANNEL_ID: not set (optional — daily alerts sent to subscribers only)"
        )

    return ok
