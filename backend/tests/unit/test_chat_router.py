"""The keyword fallback of the chatbot: which tool does a question lead to? (no database, no LLM)"""
import pytest

from app.chat.router import route

NAMES = ["BlackRock", "Goldman Sachs", "Goldman Sachs Asset Management", "E*TRADE", "UBS", "Bank of America", "BofA Securities"]


@pytest.mark.parametrize("question, expected", [
    # the four sample questions of the brief
    ("How many times did settlement fail for BlackRock?", ("failures_by_counterparty", {"name": "BlackRock"})),
    ("How many times did settlement fail for INFY?", ("failures_by_symbol", {"symbol": "INFY"})),
    ("Which rule was triggered for T110003?", ("rules_triggered", {"trade_id": "T110003"})),
    ("Which trades settle in the next 2 weeks?", ("trades_settling_within", {"days": 14})),
    # counterparties: case, longest name first, first word only
    ("settlement failures for blackrock please", ("failures_by_counterparty", {"name": "BlackRock"})),
    ("how often did Goldman Sachs Asset Management fail", ("failures_by_counterparty", {"name": "Goldman Sachs Asset Management"})),
    ("how many failures for goldman?", ("failures_by_counterparty", {"name": "Goldman Sachs"})),
    ("How many times did settlement fail for E*TRADE", ("failures_by_counterparty", {"name": "E*TRADE"})),
    # symbols
    ("how many failures for symbol brk.b", ("failures_by_symbol", {"symbol": "BRK.B"})),
    ("How many times did AAPL fail?", ("failures_by_symbol", {"symbol": "AAPL"})),
    # trades
    ("what rules fired on trade t110003", ("rules_triggered", {"trade_id": "T110003"})),
    ("Why is T110003 an exception?", ("explain_trade", {"trade_id": "T110003"})),
    ("explain HT000123", ("explain_trade", {"trade_id": "HT000123"})),
    # settlement windows
    ("Trades settling in the next one week?", ("trades_settling_within", {"days": 7})),
    ("what settles within 10 days", ("trades_settling_within", {"days": 10})),
    ("settlements in the coming month", ("trades_settling_within", {"days": 30})),
    ("which trades settle in the next couple of weeks", ("trades_settling_within", {"days": 14})),
    # rankings
    ("Top 5 riskiest counterparties", ("top_risky", {"entity": "counterparty", "n": 5})),
    ("top 3 risky symbols", ("top_risky", {"entity": "symbol", "n": 3})),
    ("who are the riskiest counterparties", ("top_risky", {"entity": "counterparty", "n": 5})),
])
def test_question_leads_to_the_right_tool(question, expected):
    assert route(question, NAMES) == expected


@pytest.mark.parametrize("question", ["hello", "what can you do?", "thanks!", "'; DROP TABLE trades; --", "fail"])
def test_other_text_matches_nothing(question):
    assert route(question, NAMES) is None
