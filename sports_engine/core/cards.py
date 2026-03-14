# core/cards.py

def expected_cards(xg_home, xg_away):
    """
    Estimación simple de tarjetas basada en intensidad del partido.
    A mayor xG total, más duelos, más faltas.
    """

    xg_total = xg_home + xg_away

    # Base promedio en ligas europeas
    base_cards = 3.8

    # Ajuste por intensidad ofensiva
    estimated_cards = base_cards + (xg_total * 0.35)

    return round(estimated_cards, 1)
