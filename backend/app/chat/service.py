"""Chat orchestration (endpoint 52): LLM if configured, keyword fallback otherwise - same tools either way.

Every question and answer is stored in chat_messages and audited as CHAT_QUERY.
"""
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat import llm, router, tools
from app.models import ChatMessage, Counterparty, User
from app.services import audit_service

logger = logging.getLogger("app.chat")


def _fallback(db: Session, user: User, message: str) -> tuple[str, str | None, Any]:
    names = list(db.scalars(select(Counterparty.name)))
    routed = router.route(message, names)
    if routed is None:
        return router.HELP, None, None
    name, arguments = routed
    result = tools.run(db, user, name, arguments)
    return result.answer, name, result.data


def answer(db: Session, user: User, session_id: str, message: str, ip_address: str | None) -> tuple[str, str | None, Any]:
    mode = "fallback"
    reply: tuple[str, str | None, Any] | None = None
    if llm.enabled():
        try:
            reply = llm.ask(message, lambda name, arguments: tools.run(db, user, name, arguments))
            mode = "llm"
        except llm.LLMError as exc:  # no internet, bad key, odd reply ... the demo must still work
            logger.warning("%s - using the keyword fallback", exc)
    if reply is None:
        reply = _fallback(db, user, message)
    text, tool_used, data = reply

    db.add(ChatMessage(user_id=user.id, session_id=session_id, role="user", content=message))
    db.add(ChatMessage(user_id=user.id, session_id=session_id, role="assistant", content=text, tool_used=tool_used,
                       tool_result=audit_service._plain_json({"data": data}) if data is not None else None))
    audit_service.log(db, "CHAT_QUERY", "CHAT", actor=user, entity_id=session_id, ip_address=ip_address,
                      details={"question": message[:300], "toolUsed": tool_used, "mode": mode})
    db.commit()
    logger.info("Chat by %s: tool=%s mode=%s", user.username, tool_used, mode)
    return text, tool_used, data
