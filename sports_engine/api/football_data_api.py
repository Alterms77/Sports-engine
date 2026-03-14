import os
import requests

# Intentamos obtener la clave de la variable de entorno, 
# si no existe, usamos la que proporcionaste directamente.
API_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN") or "326817dbace2d3e8eadc29be1d404a17"
BASE_URL = "https://api.football-data.org/v4"

if not API_TOKEN:
    raise RuntimeError("API token no encontrado")

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
        print("Error en la API:", e)
        print("Respuesta de la API:", r.text)
        return []

# --- Ejemplo de uso ---
# matches = get_matches("PL", date_from="2024-01-01", date_to="2024-12-31")
# print(matches[0] if matches else "No hay partidos.")