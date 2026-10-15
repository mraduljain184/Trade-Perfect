"""BE-7: chatbot (endpoint 52). No LLM key is set in the tests, so the keyword fallback answers -
except in the tests that plug in a fake LLM."""
import json
from datetime import date

import httpx
import pytest
from sqlalchemy import func, or_, select

from app.chat import llm
from app.core.config import get_settings
from app.models import AuditLog, ChatMessage, HistoricalTrade, SystemConfig, Trade, User
from app.services import engine_adapter
from tests.conftest import ROLE_USERS, assert_error_shape

CHAT, TRADES = "/api/v1/chat", "/api/v1/trades"
TRADER, OTHER_TRADER, MANAGER = "james.anderson", "laura.martinez", "ops.manager"
HEADER = "tradeId,tradeType,symbol,quantity,price,counterparty,tradeDate,settlementDate,accountId,ssiMatched,currencyMatched,traderName"
ROWS = [
    "CHT00001,BUY,AAPL,1000,10250.5,BlackRock,2026-09-29,2026-10-01,ACC1000,NO,NO,James Anderson",     # EXCEPTION 0.75
    "CHT00002,BUY,MSFT,9000,10250.5,Jefferies,2026-09-29,2026-10-05,ACC1000,YES,YES,James Anderson",   # PASS 0.10
    "CHT00003,BUY,AAPL,1000,10250.5,Jefferies,2026-09-29,2026-11-20,ACC1000,YES,YES,James Anderson",   # settles later
    "CHT00004,BUY,ISRG,1000,10250.5,BlackRock,2026-09-29,2026-10-01,ACC1011,YES,YES,James Anderson",   # REJECTED (2 gates)
]


@pytest.fixture(autouse=True, scope="module")
def _data(client, login, _database):
    """Business date 2026-09-28, no LLM key, and four trades uploaded by one trader."""
    from app.core.database import SessionLocal

    def business_date(value):
        with SessionLocal() as session:
            session.get(SystemConfig, "business_date_override").value = value
            session.commit()
        engine_adapter.invalidate_cache()

    business_date("2026-09-28")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(get_settings(), "llm_api_key", "")
        text = "\r\n".join([HEADER, *ROWS]) + "\r\n"
        assert client.post(f"{TRADES}/bulk", files={"file": ("chat.csv", text.encode(), "text/csv")},
                           headers=login(TRADER)).status_code == 202
        yield
    business_date(None)


def ask(client, login, message, user=MANAGER, session="s-1"):
    response = client.post(CHAT, json={"sessionId": session, "message": message}, headers=login(user))
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"answer", "toolUsed", "data"}
    return response.json()


# ---------------------------------------------------------------- the four sample questions, no API key
def test_failures_for_a_counterparty(client, login, db):
    body = ask(client, login, "How many times did settlement fail for BlackRock?")
    live = dict(db.execute(select(Trade.status, func.count()).where(Trade.counterparty == "BlackRock").group_by(Trade.status)).all())
    assert body["toolUsed"] == "failures_by_counterparty"
    assert "2,049 times for BlackRock in the historical data" in body["answer"]            # section 2.3
    assert f"{live.get('EXCEPTION', 0):,} EXCEPTION and {live.get('REJECTED', 0):,} REJECTED" in body["answer"]
    assert body["data"]["historicalFailures"] == 2049 and body["data"]["liveException"] == live.get("EXCEPTION", 0) >= 1


def test_failures_for_a_symbol(client, login, db):
    total, failed = db.execute(select(func.count(), func.count().filter(HistoricalTrade.settlement_failed))
                               .where(HistoricalTrade.symbol == "INFY")).one()
    body = ask(client, login, "How many times did settlement fail for INFY?")
    assert body["toolUsed"] == "failures_by_symbol" and body["data"]["name"] == "INFY"
    assert (body["data"]["historicalFailures"], body["data"]["historicalTrades"]) == (failed, total)
    assert (f"{failed:,} times for symbol INFY" in body["answer"]) if total else ("no historical trades" in body["answer"])
    unknown = ask(client, login, "How many times did settlement fail for ZZZZ?")
    assert "not in the symbol master" in unknown["answer"] and unknown["data"]["historicalTrades"] == 0


def test_which_rule_was_triggered(client, login):
    body = ask(client, login, "Which rule was triggered for CHT00001?")
    assert body["toolUsed"] == "rules_triggered"
    assert "CHT00001 is EXCEPTION with a risk score of 0.75" in body["answer"]
    assert {r["ruleId"] for r in body["data"]} == {"S-SSI", "S-CCY", "S-CP-TIER"}
    assert body["data"][0] == {"ruleId": "S-SSI", "category": "SSI", "contribution": 0.35, "message": body["data"][0]["message"]}
    assert all(r["ruleId"] in body["answer"] for r in body["data"])
    rejected = ask(client, login, "which rules were triggered for cht00004")              # hard gates of a rejected row
    assert {r["ruleId"] for r in rejected["data"]} == {"R-005", "R-007"} and "REJECTED" in rejected["answer"]


def test_trades_settling_in_the_next_two_weeks(client, login, db):
    body = ask(client, login, "Which trades settle in the next 2 weeks?")
    window = (Trade.settlement_date >= date(2026, 9, 28), Trade.settlement_date <= date(2026, 10, 12))
    counts = dict(db.execute(select(Trade.status, func.count()).where(*window).group_by(Trade.status)).all())
    assert body["toolUsed"] == "trades_settling_within"
    assert f"{sum(counts.values()):,} trades settle between 2026-09-28 and 2026-10-12 (next 14 days)" in body["answer"]
    for status, count in counts.items():
        assert f"{count:,} {status}" in body["answer"]
    scores = [row["finalScore"] for row in body["data"]]
    assert scores == sorted(scores, reverse=True) and len(body["data"]) <= 20             # riskiest first
    assert set(body["data"][0]) == {"tradeId", "settlementDate", "status", "finalScore", "counterparty", "symbol"}
    assert all("2026-09-28" <= row["settlementDate"] <= "2026-10-12" for row in body["data"])
    mine = ask(client, login, "Which trades settle in the next 2 weeks?", user=TRADER)
    assert {row["tradeId"] for row in mine["data"]} == {"CHT00001", "CHT00002"} and "your own trades only" in mine["answer"]


# ---------------------------------------------------------------- the other tools
def test_explain_a_trade_and_rank_counterparties(client, login):
    why = ask(client, login, "Why is CHT00001 an exception?")
    assert why["toolUsed"] == "explain_trade" and "CHT00001" in why["answer"] and "Suggested action" in why["answer"]
    assert [f["factor"] for f in why["data"]] == ["SSI", "COUNTERPARTY", "CURRENCY"]
    top = ask(client, login, "Top 3 riskiest counterparties")
    assert top["toolUsed"] == "top_risky" and 2 <= len(top["data"]) <= 3
    exceptions = [row["exceptions"] for row in top["data"]]
    assert exceptions == sorted(exceptions, reverse=True) and set(top["data"][0]) == {"name", "trades", "exceptions", "averageScore"}
    assert ask(client, login, "top 2 risky symbols")["answer"].startswith("Top 2 riskiest symbols")


def test_anything_else_gets_the_help_text(client, login):
    for message in ("hello", "what is the weather?", "'; DROP TABLE trades; --"):
        body = ask(client, login, message)
        assert body["toolUsed"] is None and body["data"] is None and "I can answer questions like" in body["answer"]


# ---------------------------------------------------------------- safety: scoping and SQL
def test_a_trader_only_gets_their_own_trades(client, login, db):
    assert "CHT00001 is EXCEPTION" in ask(client, login, "Which rule was triggered for CHT00001?", user=TRADER)["answer"]
    other = ask(client, login, "Which rule was triggered for CHT00001?", user=OTHER_TRADER)
    assert "could not find trade CHT00001" in other["answer"] and other["data"] is None
    assert "could not find trade CHT00001" in ask(client, login, "explain CHT00001", user=OTHER_TRADER)["answer"]

    me = db.scalar(select(User.id).where(User.username == TRADER))
    own = db.scalar(select(func.count()).select_from(Trade).where(
        Trade.counterparty == "BlackRock", Trade.status == "EXCEPTION", or_(Trade.trader_user_id == me, Trade.created_by == me)))
    body = ask(client, login, "How many times did settlement fail for BlackRock?", user=TRADER)
    assert body["data"]["liveException"] == own == 1 and "your own trades only" in body["answer"]
    assert body["data"]["historicalFailures"] == 2049                                      # history is not personal data


def test_a_user_without_trade_access_gets_history_but_no_live_trades(client, login):
    body = ask(client, login, "How many times did settlement fail for BlackRock?", user="ml.admin")
    assert body["data"]["historicalFailures"] == 2049 and body["data"]["liveException"] == 0
    assert "no access to live trades" in body["answer"]
    assert "could not find trade" in ask(client, login, "Which rule was triggered for CHT00001?", user="ml.admin")["answer"]
    assert ask(client, login, "Which trades settle in the next 2 weeks?", user="ml.admin")["data"] == []


def test_user_text_is_only_ever_a_bound_parameter(client, login, db):
    before = db.scalar(select(func.count()).select_from(Trade))
    for message in ("How many times did settlement fail for BlackRock'; DROP TABLE trades; --",
                    "Which rule was triggered for T1' OR '1'='1",
                    "how many failures for symbol X%' OR 1=1 --"):
        assert ask(client, login, message)["answer"]
    assert db.scalar(select(func.count()).select_from(Trade)) == before


# ---------------------------------------------------------------- logging
def test_every_question_is_stored_and_audited(client, login, db):
    ask(client, login, "Which rule was triggered for CHT00001?", session="log-test-1")
    rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == "log-test-1").order_by(ChatMessage.id)).all()
    assert [(r.role, r.tool_used) for r in rows] == [("user", None), ("assistant", "rules_triggered")]
    assert rows[0].content == "Which rule was triggered for CHT00001?" and "S-SSI" in rows[1].content
    assert len(rows[1].tool_result["data"]) == 3
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "CHAT_QUERY").order_by(AuditLog.id.desc()))
    assert audit.actor_username == MANAGER and audit.entity_id == "log-test-1"
    assert audit.details == {"question": "Which rule was triggered for CHT00001?", "toolUsed": "rules_triggered", "mode": "fallback"}


# ---------------------------------------------------------------- permissions + validation
@pytest.mark.parametrize("username", ROLE_USERS.values())
def test_every_role_may_use_the_chatbot(client, login, username):
    assert ask(client, login, "Top 5 riskiest counterparties", user=username)["toolUsed"] == "top_risky"


def test_no_token_and_bad_bodies(client, login):
    assert_error_shape(client.post(CHAT, json={"sessionId": "s", "message": "hi"}), 401, "UNAUTHORIZED")
    for body in ({"message": "hi"}, {"sessionId": "s"}, {"sessionId": "s", "message": "   "},
                 {"sessionId": "x" * 51, "message": "hi"}, {"sessionId": "s", "message": "x" * 1001}):
        assert_error_shape(client.post(CHAT, json=body, headers=login(MANAGER)), 422, "VALIDATION_ERROR")


# ---------------------------------------------------------------- with an LLM (faked)
class FakeLLM:
    """Stands in for an OpenAI-compatible server: records requests, replies from a queue."""

    def __init__(self, patch, *replies):
        self.requests, self.replies = [], list(replies)
        patch.setattr(get_settings(), "llm_api_key", "test-key-not-real")
        patch.setattr(get_settings(), "llm_base_url", "https://llm.example/v1/")
        patch.setattr(llm.httpx, "post", self.post)

    def post(self, url, json=None, headers=None, timeout=None):   # noqa: A002 - httpx's own argument name
        self.requests.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json={"choices": [{"message": reply}]}, request=httpx.Request("POST", url))


def tool_call(tool, **arguments):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]}


def test_llm_picks_the_tool_and_phrases_the_answer(client, login, db, monkeypatch):
    fake = FakeLLM(monkeypatch, tool_call("failures_by_counterparty", name="blackrock"),
                   {"role": "assistant", "content": "BlackRock had 2,049 historical settlement failures."})
    body = ask(client, login, "did blackrock have settlement problems?", session="llm-1")
    assert body == {"answer": "BlackRock had 2,049 historical settlement failures.", "toolUsed": "failures_by_counterparty",
                    "data": body["data"]} and body["data"]["historicalFailures"] == 2049

    first, second = fake.requests
    assert first["url"] == "https://llm.example/v1/chat/completions" and first["timeout"] == 20
    assert first["headers"] == {"Authorization": "Bearer test-key-not-real"}
    assert {t["function"]["name"] for t in first["json"]["tools"]} == {
        "failures_by_counterparty", "failures_by_symbol", "rules_triggered", "explain_trade", "trades_settling_within", "top_risky"}
    assert first["json"]["messages"][-1] == {"role": "user", "content": "did blackrock have settlement problems?"}
    assert second["json"]["messages"][-1]["role"] == "tool" and "2049" in second["json"]["messages"][-1]["content"]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "CHAT_QUERY").order_by(AuditLog.id.desc()))
    assert audit.details["mode"] == "llm" and "test-key-not-real" not in str(audit.details)


def test_llm_tool_calls_respect_the_callers_scope(client, login, monkeypatch):
    FakeLLM(monkeypatch, tool_call("rules_triggered", trade_id="CHT00001"), {"role": "assistant", "content": ""})
    body = ask(client, login, "what fired on CHT00001", user=OTHER_TRADER)               # not their trade
    assert "could not find trade CHT00001" in body["answer"] and body["data"] is None    # empty phrasing -> tool's own text


@pytest.mark.parametrize("replies", [
    [httpx.ConnectError("no internet")],                                                  # cannot reach the LLM
    [tool_call("drop_everything", table="trades")],                                       # a tool that does not exist
    [tool_call("rules_triggered", sql="SELECT 1")],                                       # an argument that does not exist
    [{"role": "assistant", "content": ""}],                                               # empty reply
])
def test_when_the_llm_fails_the_keyword_fallback_still_answers(client, login, db, monkeypatch, replies):
    FakeLLM(monkeypatch, *replies)
    body = ask(client, login, "Which rule was triggered for CHT00001?")
    assert body["toolUsed"] == "rules_triggered" and "S-SSI" in body["answer"]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "CHAT_QUERY").order_by(AuditLog.id.desc()))
    assert audit.details["mode"] == "fallback"


def test_llm_phrasing_failure_keeps_the_tools_own_answer(client, login, monkeypatch):
    FakeLLM(monkeypatch, tool_call("trades_settling_within", days=14), httpx.ReadTimeout("slow"))
    body = ask(client, login, "what is settling soon?")
    assert body["toolUsed"] == "trades_settling_within" and "settle between 2026-09-28 and 2026-10-12" in body["answer"]
