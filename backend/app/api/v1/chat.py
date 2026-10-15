"""Chat endpoint 52 (PROJECT_CONTEXT section 8.1)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.chat import service
from app.core.deps import client_ip, get_db, require_permission
from app.models import User
from app.schemas.base import ERR_AUTH
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("", response_model=ChatResponse, responses=ERR_AUTH,
             summary="Ask about failures, rules, upcoming settlements or risky counterparties")
def chat(body: ChatRequest, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_permission("CHATBOT_USE"))) -> ChatResponse:
    text, tool_used, data = service.answer(db, user, body.session_id, body.message, client_ip(request))
    return ChatResponse(answer=text, tool_used=tool_used, data=data)
