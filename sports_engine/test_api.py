from api.football_data_api import get_matches

print("Probando API Football-Data...")

matches = get_matches(
    "PL",
    date_from="2024-01-01",
    date_to="2024-12-31"
)

print("Partidos encontrados:", len(matches))

for m in matches[:5]:
    print(m["homeTeam"]["name"], "vs", m["awayTeam"]["name"])