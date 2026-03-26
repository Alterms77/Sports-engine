import os
import sys
import requests
import logging

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SPORTS_ENGINE_DIR = os.path.dirname(_THIS_DIR)
if _SPORTS_ENGINE_DIR not in sys.path:
    sys.path.insert(0, _SPORTS_ENGINE_DIR)

from core.config import API_SPORTS_KEY, FOOTBALL_DATA_TOKEN

# Football-Data.org uses its own token; falls back to API_SPORTS_KEY when not set
API_TOKEN = FOOTBALL_DATA_TOKEN or API_SPORTS_KEY
BASE_URL = "https://api.football-data.org/v4"

if not API_TOKEN:
    logger.warning("No football-data API token found; football_data_api will be unavailable")

headers = {
    "X-Auth-Token": API_TOKEN
}

def get_matches(competition_code, season=None, date_from=None, date_to=None):
    """
    Obtiene los partidos de una competición específica.
    Ejemplo de competition_code: 'PL' (Premier League), 'CL' (Champions League)

    Parámetros opcionales:
    season -> temporada (ej: 2023)
    date_from -> fecha inicio (ej: "2024-01-01")
    date_to -> fecha fin (ej: "2024-12-31")
    """

    url = f"{BASE_URL}/competitions/{competition_code}/matches"

    params = {}

    if season:
        params["season"] = season

    if date_from:
        params["dateFrom"] = date_from

    if date_to:
        params["dateTo"] = date_to

    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()

        data = r.json()

        return data.get("matches", [])

    except requests.exceptions.HTTPError as e:
        logger.error("Error en la API: %s", e)
        logger.error("Respuesta de la API: %s", r.text)
        return []

# --- Ejemplo de uso ---
# matches = get_matches("PL", date_from="2024-01-01", date_to="2024-12-31")
# print(matches[0] if matches else "No hay partidos.")