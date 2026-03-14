import os
import sys
import requests
import logging

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SPORTS_ENGINE_DIR = os.path.dirname(_THIS_DIR)
if _SPORTS_ENGINE_DIR not in sys.path:
    sys.path.insert(0, _SPORTS_ENGINE_DIR)

from core.config import API_SPORTS_KEY, API_SPORTS_BASE_URL

BASE_URL = API_SPORTS_BASE_URL

headers = {
    "x-apisports-key": API_SPORTS_KEY
}

def get_matches(league_id, season):

    url = f"{BASE_URL}/fixtures"

    params = {
        "league": league_id,
        "season": season
    }

    r = requests.get(url, headers=headers, params=params)

    data = r.json()

    return data.get("response", [])