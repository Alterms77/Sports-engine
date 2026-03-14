# core/teams.py

import unicodedata
from typing import Optional

# =========================
# NORMALIZADOR DE TEXTO
# =========================
def clean(text: str) -> str:
    if not text:
        return ""

    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace(".", "").replace(",", "")
    text = text.replace("-", " ")
    text = " ".join(text.split())
    return text


# =========================
# BASE DE DATOS GLOBAL
# =========================

TEAMS = {

    # ===================== LIGA MX =======================

   
    "club america": "Club América",
    "america": "Club América",

    "guadalajara": "Club Deportivo Guadalajara",
    "cd guadalajara": "Club Deportivo Guadalajara",

    "puebla": "Club Puebla",
    "club puebla": "Club Puebla",

    "necaxa": "Necaxa",

    "tigres": "Tigres UANL",
    "tigres uanl": "Tigres UANL",

    "monterrey": "CF Monterrey",
    "cf monterrey": "CF Monterrey",

    "santos": "Santos Laguna",
    "santos laguna": "Santos Laguna",

    "atlas": "Atlas FC",

    "toluca": "Deportivo Toluca",

    "queretaro": "Querétaro FC",
    "queretaro fc": "Querétaro FC",

    "fc juarez": "FC Juárez",
    "juarez": "FC Juárez",

    "mazatlan": "Mazatlán FC",
    "mazatlan fc": "Mazatlán FC",

    "tijuana": "Club Tijuana",
    "xolos": "Club Tijuana",

    "atletico san luis": "Atlético San Luis",
    "san luis": "Atlético San Luis",

    # ================= PREMIER LEAGUE ===================

    "arsenal": "Arsenal FC",
    "arsenal fc": "Arsenal FC",

    "chelsea": "Chelsea FC",

    "manchester city": "Manchester City",
    "man city": "Manchester City",

    "manchester united": "Manchester United",
    "man united": "Manchester United",

    "liverpool": "Liverpool FC",

    "tottenham": "Tottenham Hotspur",
    "spurs": "Tottenham Hotspur",

    "newcastle": "Newcastle United",

    "aston villa": "Aston Villa",

    "west ham": "West Ham United",

    "brighton": "Brighton & Hove Albion",

    "wolves": "Wolverhampton Wanderers",
    "wolverhampton": "Wolverhampton Wanderers",

    "everton": "Everton FC",

    "fulham": "Fulham FC",

    "brentford": "Brentford FC",

    "nottingham forest": "Nottingham Forest",

    "crystal palace": "Crystal Palace",

    "burnley": "Burnley FC",

    "sheffield united": "Sheffield United",

    "luton": "Luton Town",

    "bournemouth": "AFC Bournemouth",

    # ===================== LALIGA ========================

    "real madrid": "Real Madrid CF",

    "barcelona": "FC Barcelona",
    "fc barcelona": "FC Barcelona",

    "atletico madrid": "Atlético de Madrid",

    "sevilla": "Sevilla FC",

    "villarreal": "Villarreal CF",

    "real sociedad": "Real Sociedad",

    "betis": "Real Betis",

    "athletic club": "Athletic Club",
    "athletic bilbao": "Athletic Club",

    "valencia": "Valencia CF",

    "celta": "Celta de Vigo",

    "osasuna": "CA Osasuna",

    "getafe": "Getafe CF",

    "alaves": "Deportivo Alavés",

    "mallorca": "RCD Mallorca",

    "granada": "Granada CF",

    "cadiz": "Cádiz CF",

    "las palmas": "UD Las Palmas",

    "girona": "Girona FC",

    "rayo vallecano": "Rayo Vallecano",

    # ===================== SERIE A =======================

    "juventus": "Juventus",

    "inter": "Inter Milan",
    "internazionale": "Inter Milan",

    "milan": "AC Milan",
    "ac milan": "AC Milan",

    "napoli": "Napoli",

    "roma": "AS Roma",

    "lazio": "Lazio",

    "atalanta": "Atalanta",

    "fiorentina": "Fiorentina",

    "bologna": "Bologna",

    "torino": "Torino",

    "genoa": "Genoa",

    "udinese": "Udinese",

    "sassuolo": "Sassuolo",

    "lecce": "Lecce",

    "cagliari": "Cagliari",

    "empoli": "Empoli",

    "verona": "Hellas Verona",

    "salernitana": "Salernitana",

    "monza": "Monza",

    # =================== BUNDESLIGA =====================

    "bayern": "Bayern Munich",
    "bayern munich": "Bayern Munich",

    "borussia dortmund": "Borussia Dortmund",
    "dortmund": "Borussia Dortmund",

    "leverkusen": "Bayer Leverkusen",

    "rb leipzig": "RB Leipzig",

    "eintracht frankfurt": "Eintracht Frankfurt",

    "wolfsburg": "VfL Wolfsburg",

    "stuttgart": "VfB Stuttgart",

    "freiburg": "SC Freiburg",

    "union berlin": "Union Berlin",

    "werder bremen": "Werder Bremen",

    "augsburg": "FC Augsburg",

    "hoffenheim": "TSG Hoffenheim",

    "mainz": "Mainz 05",

    "koln": "FC Köln",

    "bochum": "VfL Bochum",

    "darmstadt": "Darmstadt 98",

    "heidenheim": "Heidenheim",
}


# =========================
# FUNCIÓN PRINCIPAL (MEJORADA)
# =========================
def normalize_team(name: str) -> Optional[str]:
    """
    Devuelve el nombre canónico del equipo o None si no se encuentra.
    Incluye fallback por coincidencia parcial.
    """
    key = clean(name)

    # 1) Coincidencia exacta
    if key in TEAMS:
        return TEAMS[key]

    # 2) Coincidencia parcial (MUY IMPORTANTE)
    for alias, official in TEAMS.items():
        if key in alias or alias in key:
            return official

    return None


# =========================
# UTILIDAD DE DEBUG (opcional)
# =========================
def debug_team(name: str) -> dict:
    return {
        "input": name,
        "cleaned": clean(name),
        "found": normalize_team(name),
    }
