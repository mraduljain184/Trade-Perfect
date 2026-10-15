"""Keyword fallback (PROJECT_CONTEXT section 11): picks a tool from the question with plain rules.

Used when LLM_API_KEY is empty or the LLM call fails, so the chatbot works with no internet.
`route()` is a pure function: it needs the question and the list of counterparty names, no database.
"""
import re

HELP = ("I can answer questions like: \"How many times did settlement fail for BlackRock?\", "
        "\"How many times did settlement fail for INFY?\", \"Which rule was triggered for T110003?\", "
        "\"Why is T110003 an exception?\", \"Which trades settle in the next 2 weeks?\" or "
        "\"Top 5 riskiest counterparties\".")

_TRADE_ID = re.compile(r"\b([A-Za-z]{1,6}\d{3,})\b")
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "couple": 2, "few": 3}
_UNIT_DAYS = {"day": 1, "week": 7, "fortnight": 14, "month": 30}
_PERIOD = re.compile(r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")?\s*(?:of\s+)?(day|week|fortnight|month)s?\b")
_AFTER_FOR = re.compile(r"\b(?:for|of|symbol|ticker|in)\s+(?:symbol\s+|ticker\s+|the\s+)?([A-Za-z][A-Za-z0-9.&]{0,11})\b")
_STOP_WORDS = {"THE", "A", "AN", "ALL", "ANY", "EACH", "EVERY", "TRADE", "TRADES", "SETTLEMENT", "SETTLEMENTS", "FAIL",
               "FAILED", "FAILURE", "FAILURES", "COUNTERPARTY", "SYMBOL", "TICKER", "ME", "US", "IT", "THIS", "THAT",
               "LIVE", "HISTORY", "HISTORICAL", "DATA", "TOTAL", "MANY", "TIMES"}


def _days(text: str) -> int:
    match = _PERIOD.search(text)
    if not match:
        return 7
    amount, unit = match.groups()
    count = int(amount) if amount and amount.isdigit() else _NUMBER_WORDS.get(amount or "", 1)
    return min(max(count * _UNIT_DAYS[unit], 1), 365)


def _counterparty(text: str, names: list[str]) -> str | None:
    """Longest full name found in the question; else a name whose first word (4+ letters) is mentioned."""
    for name in sorted(names, key=len, reverse=True):
        if name.lower() in text:
            return name
    words = set(re.findall(r"[a-z0-9*&.]+", text))
    for name in sorted(names, key=len):
        first = name.lower().split()[0]
        if len(first) >= 4 and first in words:
            return name
    return None


def route(message: str, counterparty_names: list[str]) -> tuple[str, dict] | None:
    """(tool name, arguments) for the question, or None when no rule matches."""
    text = " ".join(message.lower().split())
    trade = _TRADE_ID.search(message)
    trade_id = trade.group(1).upper() if trade and not re.fullmatch(r"(?i)top\d+", trade.group(1)) else None

    if trade_id and re.search(r"\b(rule|rules|trigger|triggered|gate|gates|validation)\b", text):
        return "rules_triggered", {"trade_id": trade_id}
    if trade_id:
        return "explain_trade", {"trade_id": trade_id}
    if "settl" in text and re.search(r"\b(next|within|coming|upcoming|following|in the)\b", text) and _PERIOD.search(text):
        return "trades_settling_within", {"days": _days(text)}
    if re.search(r"\b(top|riskiest|most risky|highest risk|worst)\b", text):
        count = re.search(r"\btop\s*(\d+)\b", text)
        entity = "symbol" if re.search(r"\b(symbol|symbols|ticker|tickers|stock|stocks)\b", text) else "counterparty"
        return "top_risky", {"entity": entity, "n": int(count.group(1)) if count else 5}
    if re.search(r"\b(fail|fails|failed|failure|failures|how many|how often)\b", text):
        name = _counterparty(text, counterparty_names)
        if name:
            return "failures_by_counterparty", {"name": name}
        candidates = [c for c in _AFTER_FOR.findall(message) if c.upper() not in _STOP_WORDS]
        capitals = [w for w in re.findall(r"\b[A-Z][A-Z0-9.]{1,9}\b", message) if w not in _STOP_WORDS]
        symbol = (candidates or capitals or [None])[-1]
        if symbol:
            return "failures_by_symbol", {"symbol": symbol.upper()}
    return None
