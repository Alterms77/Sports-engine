from api.api_football import get_matches

print("Probando API-Football...")

matches = get_matches(262, 2024)  # 262 = Liga MX

print("Partidos encontrados:", len(matches))

for m in matches[:5]:
    home = m["teams"]["home"]["name"]
    away = m["teams"]["away"]["name"]

    print(home, "vs", away)