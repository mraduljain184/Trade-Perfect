"""The chatbot's tools (PROJECT_CONTEXT section 11). Each one runs fixed, parameterised SQL.

Safety rules:
  * User text never becomes part of a SQL string: it is only ever passed as a bound parameter.
  * Live-trade data follows the same row scoping as the trade APIs: a Trader only gets their own
    trades; a user without TRADE_VIEW gets no live-trade data at all (historical totals are open
    to everyone who may use the chatbot).
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import Select, case, false, func, or_, select
from sqlalchemy.orm import Session

from app.models import Counterparty, HistoricalTrade, RuleEvaluation, Symbol, Trade, User
from app.services import engine_adapter

MAX_ROWS = 20


@dataclass
class ToolResult:
    answer: str
    data: Any = None


# ------------------------------------------------------------------ helpers
def _scope(stmt: Select, user: User) -> Select:
    scope = user.permission_scope("TRADE_VIEW")
    if scope == "ALL":
        return stmt
    if scope == "OWN":
        return stmt.where(or_(Trade.trader_user_id == user.id, Trade.created_by == user.id))
    return stmt.where(false())


def _whose(user: User) -> str:
    scope = user.permission_scope("TRADE_VIEW")
    return "" if scope == "ALL" else " (your own trades only)" if scope == "OWN" else " (you have no access to live trades)"


def _failures(db: Session, user: User, column_hist, column_live, value: str, label: str) -> ToolResult:
    total, failed = db.execute(select(func.count(), func.count().filter(HistoricalTrade.settlement_failed))
                               .where(column_hist == value)).one()
    live = dict(db.execute(_scope(select(Trade.status, func.count()), user)
                           .where(column_live == value, Trade.status.in_(("EXCEPTION", "REJECTED")))
                           .group_by(Trade.status)).all())
    exceptions, rejected = live.get("EXCEPTION", 0), live.get("REJECTED", 0)
    if total:
        history = (f"Settlement failed {failed:,} times for {label} in the historical data "
                   f"({failed:,} of {total:,} trades, {100 * failed / total:.1f}%).")
    else:
        history = f"There are no historical trades for {label}."
    answer = f"{history} In live trades{_whose(user)}: {exceptions:,} EXCEPTION and {rejected:,} REJECTED."
    return ToolResult(answer, {"name": value, "historicalFailures": failed, "historicalTrades": total,
                               "liveException": exceptions, "liveRejected": rejected})


def _find_trade(db: Session, user: User, trade_id: str) -> Trade | None:
    """The non-rejected trade with this ID first, else the latest rejected one - if the user may see it."""
    stmt = (_scope(select(Trade), user).where(func.upper(Trade.trade_id) == trade_id.strip().upper())
            .order_by(case((Trade.status == "REJECTED", 1), else_=0), Trade.created_at.desc()).limit(1))
    return db.scalar(stmt)


def _not_found(trade_id: str) -> ToolResult:
    return ToolResult(f"I could not find trade {trade_id.strip().upper()} among the trades you are allowed to see.")


# ------------------------------------------------------------------ the tools
def failures_by_counterparty(db: Session, user: User, name: str) -> ToolResult:
    wanted = name.strip()
    found = db.scalar(select(Counterparty.name).where(func.lower(Counterparty.name) == wanted.lower()))
    if found is None:  # "goldman" -> "Goldman Sachs" (shortest match wins)
        found = db.scalar(select(Counterparty.name).where(Counterparty.name.ilike(f"%{wanted}%"))
                          .order_by(func.length(Counterparty.name)).limit(1))
    if found is None:
        return ToolResult(f"I do not know a counterparty called '{wanted}'.")
    return _failures(db, user, HistoricalTrade.counterparty, Trade.counterparty, found, found)


def failures_by_symbol(db: Session, user: User, symbol: str) -> ToolResult:
    wanted = symbol.strip().upper()
    known = db.scalar(select(Symbol.symbol).where(func.upper(Symbol.symbol) == wanted))
    result = _failures(db, user, HistoricalTrade.symbol, Trade.symbol, known or wanted, f"symbol {known or wanted}")
    if known is None:
        result.answer = f"{wanted} is not in the symbol master, so trades in it are rejected. " + result.answer
    return result


def rules_triggered(db: Session, user: User, trade_id: str) -> ToolResult:
    trade = _find_trade(db, user, trade_id)
    if trade is None:
        return _not_found(trade_id)
    hits = db.scalars(select(RuleEvaluation).where(
        RuleEvaluation.trade_pk == trade.id, RuleEvaluation.trade_version == trade.version,
        RuleEvaluation.triggered.is_(True)).order_by(RuleEvaluation.score_contribution.desc(), RuleEvaluation.id)).all()
    rows = [{"ruleId": h.rule_id, "category": h.rule_category, "contribution": float(h.score_contribution),
             "message": h.message} for h in hits]
    known = {row["ruleId"] for row in rows}
    rows += [{"ruleId": r.get("ruleId"), "category": "HARD_GATE", "contribution": 0.0, "message": r.get("message")}
             for r in (trade.rejection_reasons or []) if r.get("ruleId") not in known]
    head = f"Trade {trade.trade_id} is {trade.status} with a risk score of {float(trade.final_score):.2f}."
    if not rows:
        return ToolResult(f"{head} No rule was triggered for it.", [])
    listed = "; ".join(f"{r['ruleId']} - {r['message']}" + (f" (+{r['contribution']:.2f})" if r["contribution"] else "")
                       for r in rows)
    return ToolResult(f"{head} Triggered rules: {listed}.", rows)


def explain_trade(db: Session, user: User, trade_id: str) -> ToolResult:
    trade = _find_trade(db, user, trade_id)
    if trade is None:
        return _not_found(trade_id)
    text = trade.explanation or f"Trade {trade.trade_id} is {trade.status} with a risk score of {float(trade.final_score):.2f}."
    if trade.suggested_action and trade.status != "REJECTED":
        text += f" Suggested action: {trade.suggested_action}"
    return ToolResult(text, trade.risk_factors or [])


def trades_settling_within(db: Session, user: User, days: int) -> ToolResult:
    days = min(max(int(days), 1), 365)
    start = engine_adapter.get_business_date(db)
    end = start + timedelta(days=days)
    window = (Trade.settlement_date >= start, Trade.settlement_date <= end)
    counts = dict(db.execute(_scope(select(Trade.status, func.count()), user).where(*window).group_by(Trade.status)).all())
    total = sum(counts.values())
    rows = db.execute(_scope(select(Trade.trade_id, Trade.settlement_date, Trade.status, Trade.final_score,
                                    Trade.counterparty, Trade.symbol), user)
                      .where(*window, Trade.status != "REJECTED")
                      .order_by(Trade.final_score.desc(), Trade.settlement_date, Trade.trade_id).limit(MAX_ROWS)).all()
    data = [{"tradeId": r.trade_id, "settlementDate": r.settlement_date.isoformat(), "status": r.status,
             "finalScore": float(r.final_score), "counterparty": r.counterparty, "symbol": r.symbol} for r in rows]
    if not total:
        return ToolResult(f"No trades{_whose(user)} settle between {start} and {end}.", [])
    parts = ", ".join(f"{counts[s]:,} {s}" for s in ("EXCEPTION", "MONITOR", "PASS", "REJECTED") if counts.get(s))
    more = f" The table shows the {len(data)} riskiest." if total > len(data) else ""
    return ToolResult(f"{total:,} trades{_whose(user)} settle between {start} and {end} (next {days} days): {parts}.{more}", data)


def top_risky(db: Session, user: User, entity: str = "counterparty", n: int = 5) -> ToolResult:
    n = min(max(int(n), 1), MAX_ROWS)
    by_symbol = str(entity).strip().lower().startswith("sym")
    label = "symbols" if by_symbol else "counterparties"
    column = Trade.symbol if by_symbol else Trade.counterparty
    exceptions = func.count().filter(Trade.status == "EXCEPTION")
    rows = db.execute(_scope(select(column, func.count(), exceptions, func.avg(Trade.final_score)), user)
                      .where(column.is_not(None), Trade.status != "REJECTED").group_by(column)
                      .order_by(exceptions.desc(), func.avg(Trade.final_score).desc(), column).limit(n)).all()
    if rows:
        data = [{"name": name, "trades": total, "exceptions": bad, "averageScore": round(float(avg), 4)}
                for name, total, bad, avg in rows]
        listed = "; ".join(f"{d['name']} ({d['exceptions']:,} EXCEPTION of {d['trades']:,} trades, average score {d['averageScore']:.2f})"
                           for d in data)
        return ToolResult(f"Top {len(data)} riskiest {label} in live trades{_whose(user)}: {listed}.", data)
    hist = HistoricalTrade.symbol if by_symbol else HistoricalTrade.counterparty
    failed = func.count().filter(HistoricalTrade.settlement_failed)
    rows = db.execute(select(hist, func.count(), failed).group_by(hist).order_by(failed.desc(), hist).limit(n)).all()
    data = [{"name": name, "trades": total, "historicalFailures": bad} for name, total, bad in rows]
    listed = "; ".join(f"{d['name']} ({d['historicalFailures']:,} failures)" for d in data)
    return ToolResult(f"There are no live trades{_whose(user)} to rank, so this is from the historical data. "
                      f"Top {len(data)} {label} by settlement failures: {listed}.", data)


TOOLS: dict[str, Callable[..., ToolResult]] = {
    "failures_by_counterparty": failures_by_counterparty, "failures_by_symbol": failures_by_symbol,
    "rules_triggered": rules_triggered, "explain_trade": explain_trade,
    "trades_settling_within": trades_settling_within, "top_risky": top_risky,
}

# OpenAI-style function schemas, sent to the LLM so it can pick a tool.
TOOL_SCHEMAS = [
    {"name": "failures_by_counterparty", "description": "How many times settlement failed for one counterparty (historical + live).",
     "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Counterparty name, e.g. BlackRock"}}, "required": ["name"]}},
    {"name": "failures_by_symbol", "description": "How many times settlement failed for one symbol (historical + live).",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "Ticker, e.g. INFY"}}, "required": ["symbol"]}},
    {"name": "rules_triggered", "description": "Which validation / risk rules were triggered for one trade.",
     "parameters": {"type": "object", "properties": {"trade_id": {"type": "string", "description": "Trade ID, e.g. T110003"}}, "required": ["trade_id"]}},
    {"name": "explain_trade", "description": "Why a trade has its status / risk score, with its risk factors.",
     "parameters": {"type": "object", "properties": {"trade_id": {"type": "string"}}, "required": ["trade_id"]}},
    {"name": "trades_settling_within", "description": "Trades that settle within the next N days, grouped by status.",
     "parameters": {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}}, "required": ["days"]}},
    {"name": "top_risky", "description": "The riskiest counterparties or symbols.",
     "parameters": {"type": "object", "properties": {"entity": {"type": "string", "enum": ["counterparty", "symbol"]},
                                                     "n": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["entity"]}},
]
_ARGUMENTS = {s["name"]: set(s["parameters"]["properties"]) for s in TOOL_SCHEMAS}


def run(db: Session, user: User, name: str, arguments: dict[str, Any]) -> ToolResult:
    """Run one tool by name. Unknown tools / arguments raise ValueError (the caller falls back)."""
    if name not in TOOLS:
        raise ValueError(f"unknown tool {name!r}")
    clean = {}
    for key, value in (arguments or {}).items():
        if key not in _ARGUMENTS[name]:
            raise ValueError(f"unknown argument {key!r} for {name}")
        clean[key] = int(value) if key in ("days", "n") else str(value)[:100]
    return TOOLS[name](db, user, **clean)
