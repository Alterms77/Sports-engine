"""
Parlay ticket analyzer — caption / text based.

Analyzes a parlay ticket described in free-form text (typed manually or copied
from a photo caption) and returns:
  - Parsed legs (match, pick, implied probability)
  - Combined probability
  - Risk tier
  - Actionable recommendations

How it works
------------
1. ``parse_parlay_text(text)``  splits the raw string into individual legs.
2. Each leg is passed through ``parse_leg_text()`` which extracts:
   - match  ("Home vs Away")
   - pick   ("Over 2.5", "Victoria Real Madrid", …)
   - odds   (decimal ≥ 1.01  OR  American +150/-110)
   - implied probability from the odds (if present)
3. ``analyze_parlay(legs)``  fills missing probabilities via the sports
   prediction engines already in the repo (soccer, NBA, NFL, MLB), computes
   combined probability, assigns a risk tier, and builds recommendations.
4. ``format_parlay_analysis(analysis)``  returns a Markdown string ready for
   Telegram.

Accepted leg text formats (one per line or separated by ";"):
  "Burnley vs Bournemouth | Over 2.5 | @1.75"
  "Lakers vs Warriors  Moneyline  @2.10"
  "Real Madrid vs Barcelona | Victoria Real Madrid | 1.45"
  "Chiefs vs Patriots | spread | +110"
  "Tigres vs America  BTTS  -120"
  "2. Man City vs Arsenal  Over 2.5  @1.80"   ← numbered lists OK
"""

import re
import math
from typing import Optional

# ── Odds ↔ probability helpers ─────────────────────────────────────────────────

def decimal_to_prob(odds: float) -> float:
    """Decimal odds (e.g. 1.75) → implied probability (%)."""
    if odds <= 1.0:
        return 99.9
    return round(100.0 / odds, 1)


def american_to_prob(odds: int) -> float:
    """American odds (+150 or -110) → implied probability (%)."""
    if odds >= 0:
        return round(100.0 / (odds + 100) * 100.0, 1)
    return round(abs(odds) / (abs(odds) + 100) * 100.0, 1)


def _prob_from_text(text: str) -> tuple:
    """
    Scan *text* for an odds value and return (probability, odds_raw_str).

    Tries American format first (+150/-110), then decimal (1.75 / @1.75).
    Returns (None, "") when nothing is found.
    """
    # American odds: explicit sign + 2-4 digits, not a match score
    am = re.search(r'(?<!\d)([+-])(\d{2,4})(?!\d|\.\d)', text)
    if am:
        try:
            val = int(am.group(1) + am.group(2))
            # Sanity check: American odds are within the range [-9999, +9999]
            # and never exactly 0.
            if -9999 <= val <= 9999 and val != 0:
                return american_to_prob(val), am.group(0)
        except ValueError:
            pass

    # Decimal with explicit @ prefix (most common in Spanish-language books)
    dec_at = re.search(r'@\s*(\d+\.\d{1,3})', text)
    if dec_at:
        try:
            d = float(dec_at.group(1))
            if 1.01 <= d <= 100.0:
                return decimal_to_prob(d), dec_at.group(0)
        except ValueError:
            pass

    # Decimal without @ — must be at the end of the string AND have at least
    # two decimal places to avoid false hits on market lines like "Over 2.5".
    dec_plain = re.search(r'\b(\d+\.\d{2,3})\s*$', text)
    if dec_plain:
        try:
            d = float(dec_plain.group(1))
            if 1.01 <= d <= 100.0:
                return decimal_to_prob(d), dec_plain.group(0)
        except ValueError:
            pass

    return None, ""


# ── Header / noise line detection ──────────────────────────────────────────────

_NOISE_RE = re.compile(
    r'^\s*(?:'
    r'\d+\s*patas?\b'                        # "3 patas"
    r'|total\b[^a-zA-Z]'                     # "Total: $150", "Total $500"
    r'|pago\b'                               # "Pago: $500", "Pago potencial: …"
    r'|cuota\b'                              # "Cuota: @6.93", "Cuota total: …"
    r'|parlay(?:\s+\d+\s*patas?)?\s*$'       # "parlay", "parlay 3 patas"
    r'|combinada\s*$'                        # bare "combinada"
    r'|ticket\s*$'                           # bare "ticket"
    r'|boleto\s*$'                           # bare "boleto"
    r'|picks?\s*$'                           # bare "picks" / "pick"
    r'|apuesta\s*$'                          # bare "apuesta"
    r')',
    re.IGNORECASE,
)

# Keywords that signal the start of a pick description (after the away team)
_PICK_KW_RE = re.compile(
    r'\b(over|under|btts|ambos\s+marcan|moneyline|spread|h[aáa]ndicap|'
    r'victoria|win|empate|draw|goles?|gg\b|ng\b|[+-]\d)',
    re.IGNORECASE,
)


# ── Single-leg parser ──────────────────────────────────────────────────────────

def parse_leg_text(raw: str) -> Optional[dict]:
    """
    Parse one parlay leg from a raw text string.

    Returns
    -------
    dict with keys:
      ``match``       – "Home vs Away"
      ``pick``        – pick/market label (may be "?" if undetermined)
      ``odds_raw``    – raw odds string from input (e.g. "@1.75" or "+110")
      ``prob``        – implied probability % (float) or None
      ``prob_source`` – "odds", "unknown"

    Returns ``None`` for empty lines or obvious header/noise lines.
    """
    line = raw.strip()
    if not line:
        return None

    # Strip leading leg numbers like "1.", "2)", "Leg 3:"
    line = re.sub(r'^\s*(?:leg\s*)?\d+[.):\-]\s*', '', line, flags=re.IGNORECASE).strip()
    if not line:
        return None

    # Reject obvious non-leg noise
    if _NOISE_RE.match(line):
        return None

    # Extract odds from the line (odds are removed before further parsing)
    prob, odds_raw = _prob_from_text(line)
    if odds_raw:
        line = line.replace(odds_raw, " ").strip()

    # ── Split into match part and pick part ───────────────────────────────
    match_str = ""
    pick_str = ""

    # Try explicit separators first — split on ALL pipe/arrow occurrences
    # so "match | pick | @odds" (already stripped of odds) cleanly gives
    # match="match", pick="pick" instead of pick="pick |".
    for sep in ("|", "→", "->", "—"):
        if sep in line:
            parts = [p.strip() for p in line.split(sep)]
            # Drop empty fragments (e.g. a trailing | leaves an empty string)
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                match_str = parts[0]
                pick_str  = " ".join(parts[1:])
                break

    # Fall back to "vs" detection
    if not match_str:
        vs_hit = re.search(r'\s+vs?\.?\s+', line, re.IGNORECASE)
        if vs_hit:
            home_part = line[: vs_hit.start()].strip()
            rest      = line[vs_hit.end():]

            # Pick keywords signal end of away-team name
            kw_hit = _PICK_KW_RE.search(rest)
            if kw_hit:
                away_part = rest[: kw_hit.start()].strip()
                pick_str  = rest[kw_hit.start():].strip()
            else:
                # Take first 1–3 words as the away team, rest is pick
                words = rest.split()
                cut       = min(3, len(words))
                away_part = " ".join(words[:cut])
                pick_str  = " ".join(words[cut:]).strip()

            match_str = f"{home_part} vs {away_part}".strip()
        else:
            # No "vs" — keep whole line as match, pick unknown
            match_str = line

    return {
        "match":       match_str.strip(),
        "pick":        pick_str.strip() or "?",
        "odds_raw":    odds_raw,
        "prob":        prob,
        "prob_source": "odds" if prob is not None else "unknown",
    }


# ── Multi-leg parser ───────────────────────────────────────────────────────────

def parse_parlay_text(text: str) -> list:
    """
    Parse a full parlay ticket from free-form multi-line text.

    Splits on newlines and semicolons, calls ``parse_leg_text`` on each chunk,
    and returns a list of non-None leg dicts.
    """
    legs = []
    for chunk in re.split(r'[\n;]+', text):
        leg = parse_leg_text(chunk)
        if leg:
            legs.append(leg)
    return legs


# ── Model probability look-up ──────────────────────────────────────────────────

def _try_lookup_prob(match_str: str, pick_str: str) -> Optional[float]:
    """
    Try to estimate probability for a pick using the in-repo prediction engines.

    Tries soccer first (richest model), then NBA, MLB, NFL by team-name
    resolution.  Returns None on any failure so callers can degrade gracefully.

    ``fetch_live=False`` is used to keep tests/offline runs fast.
    """
    vs_hit = re.search(r'\s+vs\.?\s+', match_str, re.IGNORECASE)
    if not vs_hit:
        return None
    home = match_str[: vs_hit.start()].strip()
    away = match_str[vs_hit.end():].strip()
    if not home or not away:
        return None

    pick_lower = pick_str.lower()

    # ── Soccer ──────────────────────────────────────────────────────────────
    try:
        from sports.football import get_full_prediction
        pred = get_full_prediction(home, away, fetch_live=False)
        if "victoria" in pick_lower or "win" in pick_lower:
            if home.lower() in pick_lower:
                v = pred.get("home_win")
                if v: return round(float(v), 1)
            if away.lower() in pick_lower:
                v = pred.get("away_win")
                if v: return round(float(v), 1)
        if "empate" in pick_lower or "draw" in pick_lower:
            v = pred.get("draw")
            if v: return round(float(v), 1)
        if "over 1.5" in pick_lower:
            v = pred.get("over_1_5")
            if v: return round(float(v), 1)
        if "over 2.5" in pick_lower:
            v = pred.get("over_2_5")
            if v: return round(float(v), 1)
        if "over 3.5" in pick_lower:
            v = pred.get("over_3_5")
            if v: return round(float(v), 1)
        if "btts" in pick_lower or "ambos" in pick_lower:
            v = pred.get("btts")
            if v: return round(float(v), 1)
        # Generic fallback: best moneyline
        top = max(pred.get("home_win", 0), pred.get("away_win", 0))
        if top > 0:
            return round(float(top), 1)
    except Exception:
        pass

    # ── NBA ──────────────────────────────────────────────────────────────────
    try:
        from sports.basketball import predict_game, resolve_team
        if resolve_team(home) and resolve_team(away):
            pred = predict_game(home, away)
            if home.lower() in pick_lower:
                v = pred.get("home_win")
                if v: return round(float(v), 1)
            if away.lower() in pick_lower:
                v = pred.get("away_win")
                if v: return round(float(v), 1)
            top = max(pred.get("home_win", 0), pred.get("away_win", 0))
            if top > 0:
                return round(float(top), 1)
    except Exception:
        pass

    # ── MLB ──────────────────────────────────────────────────────────────────
    try:
        from sports.baseball import predict_game, resolve_team
        if resolve_team(home) and resolve_team(away):
            pred = predict_game(home, away)
            if home.lower() in pick_lower:
                v = pred.get("home_win")
                if v: return round(float(v), 1)
            if away.lower() in pick_lower:
                v = pred.get("away_win")
                if v: return round(float(v), 1)
            top = max(pred.get("home_win", 0), pred.get("away_win", 0))
            if top > 0:
                return round(float(top), 1)
    except Exception:
        pass

    # ── NFL ──────────────────────────────────────────────────────────────────
    try:
        from sports.american_football import predict_game, resolve_team
        if resolve_team(home) and resolve_team(away):
            pred = predict_game(home, away)
            if home.lower() in pick_lower:
                v = pred.get("home_win")
                if v: return round(float(v), 1)
            if away.lower() in pick_lower:
                v = pred.get("away_win")
                if v: return round(float(v), 1)
            top = max(pred.get("home_win", 0), pred.get("away_win", 0))
            if top > 0:
                return round(float(top), 1)
    except Exception:
        pass

    return None


# ── Full analysis ──────────────────────────────────────────────────────────────

def analyze_parlay(legs: list, try_lookup: bool = True) -> dict:
    """
    Analyse parsed parlay legs and return a full assessment.

    Parameters
    ----------
    legs        : list of dicts from ``parse_parlay_text``
    try_lookup  : when True, fill missing probabilities via prediction engines

    Returns
    -------
    dict:
      ``legs``           – enriched leg list
      ``combined_prob``  – float or None
      ``risk_label``     – "BAJA" / "MEDIA" / "ALTA" / "MUY ALTA" / "DESCONOCIDO"
      ``risk_emoji``     – matching emoji
      ``recommendations``– list of Markdown strings
    """
    enriched: list = []
    for leg in legs:
        leg = dict(leg)  # work on a copy
        if leg.get("prob") is None and try_lookup:
            model_prob = _try_lookup_prob(leg["match"], leg["pick"])
            if model_prob is not None:
                leg["prob"] = model_prob
                leg["prob_source"] = "model"
        enriched.append(leg)

    # Combined probability (legs with known prob only)
    known = [l for l in enriched if l.get("prob") is not None]
    if known:
        combined = 1.0
        for l in known:
            combined *= l["prob"] / 100.0
        combined_prob: Optional[float] = round(combined * 100.0, 1)
    else:
        combined_prob = None

    # Risk tier based on combined probability
    if combined_prob is None:
        risk_label, risk_emoji = "DESCONOCIDO", "❓"
    elif combined_prob >= 60:
        risk_label, risk_emoji = "BAJA", "🟢"
    elif combined_prob >= 40:
        risk_label, risk_emoji = "MEDIA", "🟡"
    elif combined_prob >= 20:
        risk_label, risk_emoji = "ALTA", "🔴"
    else:
        risk_label, risk_emoji = "MUY ALTA", "💀"

    return {
        "legs":            enriched,
        "combined_prob":   combined_prob,
        "risk_label":      risk_label,
        "risk_emoji":      risk_emoji,
        "recommendations": _build_recommendations(enriched, combined_prob),
    }


def _build_recommendations(legs: list, combined_prob: Optional[float]) -> list:
    """Generate actionable recommendations from the analysis."""
    recs: list = []
    n = len(legs)
    known = [l for l in legs if l.get("prob") is not None]

    # Flag individually weak legs
    for leg in known:
        p = leg["prob"]
        if p < 50:
            recs.append(
                f"⚠️ *{_esc(leg['match'])}* → _{_esc(leg['pick'])}_ tiene solo "
                f"*{p}%* de prob — es el eslabón más débil del parlay."
            )
        elif p < 60:
            recs.append(
                f"⚡ *{_esc(leg['match'])}* → _{_esc(leg['pick'])}_ ({p}%) "
                "— probabilidad moderada, valora si vale la pena."
            )

    # Combined probability advice
    if combined_prob is not None:
        if combined_prob < 10:
            recs.append(
                "🚨 Prob combinada *muy baja* (menos de 10%). "
                "Apuesta solo lo que estés dispuesto a perder."
            )
        elif combined_prob >= 60:
            recs.append("✅ Buena combinación — prob combinada sólida.")

    # Too many legs
    if n > 4 and combined_prob is not None and combined_prob < 20:
        recs.append(
            "💡 Con 5+ patas la prob combinada cae rápido. "
            "Considera una versión de 2-3 patas con las más confiables."
        )

    # Single-leg "parlay" note
    if n == 1:
        recs.append(
            "💡 Una sola pata es una apuesta directa — no hay multiplicador."
        )

    # Suggest removing the weakest leg
    if len(known) >= 3:
        weakest = min(known, key=lambda l: l["prob"])
        if weakest["prob"] < 55:
            recs.append(
                f"💡 Eliminar *{_esc(weakest['match'])}* ({weakest['prob']}%) "
                "mejoraría bastante la seguridad del parlay."
            )

    if not recs:
        recs.append(
            "✅ El parlay parece equilibrado según los datos disponibles."
        )

    return recs


def _esc(text: str) -> str:
    """Escape Markdown v1 special characters in user-provided strings.

    In regular Markdown (v1) only `_` and `*` need escaping inside
    bold/italic spans to avoid breaking the formatting.
    """
    return text.replace("_", r"\_").replace("*", r"\*")


# ── Telegram formatter ─────────────────────────────────────────────────────────

_LEG_NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]


def format_parlay_analysis(analysis: dict) -> str:
    """
    Format a parlay analysis dict into a Telegram Markdown message.

    Uses regular Markdown (v1) to avoid MarkdownV2 escaping complexity.
    Dynamic content (team names, pick labels) is escaped with ``_esc()``.

    Parameters
    ----------
    analysis : output of ``analyze_parlay``
    """
    legs          = analysis["legs"]
    combined_prob = analysis["combined_prob"]
    risk_label    = analysis["risk_label"]
    risk_emoji    = analysis["risk_emoji"]
    recs          = analysis["recommendations"]

    lines = [
        "╔══════════════════════════════════╗",
        "  📸 ANÁLISIS DE PARLAY",
        "╚══════════════════════════════════╝",
        "",
        "🎟️ *PATAS DETECTADAS*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for i, leg in enumerate(legs):
        num = _LEG_NUMBERS[i] if i < len(_LEG_NUMBERS) else f"{i + 1}."
        prob = leg.get("prob")
        prob_str = f" `{prob}%`" if prob is not None else " `?%`"
        source   = " _[modelo]_" if leg.get("prob_source") == "model" else ""
        lines.append(f"  {num} *{_esc(leg['match'])}*")
        lines.append(f"       Pick: _{_esc(leg['pick'])}_{prob_str}{source}")

    lines.append("")

    if combined_prob is not None:
        lines.append(f"📊 *Prob combinada:* `{combined_prob}%`")
    else:
        lines.append("📊 *Prob combinada:* `?` _(incluye cuotas para calcular)_")

    lines.append(f"⚠️ *Nivel de riesgo:* {risk_emoji} *{_esc(risk_label)}*")
    lines.append("")

    if recs:
        lines.append("💬 *RECOMENDACIONES*")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for rec in recs:
            lines.append(rec)
        lines.append("")

    lines.append("⚠️ _Análisis recreativo. Apuesta responsablemente._")
    return "\n".join(lines)


# ── Quick usage instructions ───────────────────────────────────────────────────

USAGE_TEXT = (
    "📸 *Análisis de Parlay por Foto o Texto*\n\n"
    "Envía la foto de tu ticket con un *caption* describiendo cada pata. "
    "O usa el comando `/checkparlay`.\n\n"
    "*Formatos aceptados (una pata por línea):*\n"
    "```\n"
    "Burnley vs Bournemouth | Over 2.5 | @1.75\n"
    "Lakers vs Warriors | Moneyline | @2.10\n"
    "Real Madrid vs Barcelona | Victoria Real Madrid | 1.45\n"
    "Chiefs vs Patriots spread +110\n"
    "```\n\n"
    "Si incluyes las cuotas (odds) calculamos la prob implícita. "
    "Si no, intentamos usar nuestro modelo de predicción."
)
