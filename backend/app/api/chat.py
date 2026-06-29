"""
Chat Router — the main gateway endpoints for LLM interactions.

ENDPOINTS:
  POST /api/v1/chat                          → stateless one-shot chat (no session)
  POST /api/v1/chat/sessions                 → create a session
  GET  /api/v1/chat/sessions                 → list sessions
  GET  /api/v1/chat/sessions/{id}            → get session with messages
  POST /api/v1/chat/sessions/{id}/prompt     → send prompt within a session

DESIGN DECISIONS:
  • The stateless /chat endpoint is the simplest way to test the gateway.
    It doesn't require creating a session first. Perfect for Postman testing,
    CI pipelines, and simple integrations.
  • The session-based endpoints add conversation history, which is essential
    for the frontend chat UI.
"""

import uuid
import json
import time
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.models.analytics import TokenUsageLog
from app.schemas.chat import (
    ChatSessionResponse,
    ChatSessionDetailResponse,
    ChatMessageResponse,
)
from app.services.llm_service import generate_completion, generate_completion_stream
from app.services.fallback import completion_with_fallback
from app.utils.responses import success_response, llm_response
from app.providers import resolve_litellm_model, get_default_model
from app.core.llm_router import list_virtual_models

logger = logging.getLogger("gateway.chat")

router = APIRouter()


# ───────────────────────────────────────────────────────────────
# REQUEST / RESPONSE SCHEMAS
# ───────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """
    Schema for the stateless POST /chat endpoint.

    Example payload:
    {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "message": "Explain Docker in 3 sentences.",
      "temperature": 0.7,
      "max_tokens": 1024,
      "stream": false,
      "use_fallback": true
    }
    """
    provider: str = Field(..., description="Provider ID, e.g. 'openai', 'gemini', 'deepseek'")
    model: Optional[str] = Field(None, description="Model ID. If omitted, uses provider's default model.")
    message: str = Field(..., min_length=1, description="The user's prompt text")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=32768)
    stream: bool = Field(False, description="Enable Server-Sent Events streaming")
    use_fallback: bool = Field(True, description="Auto-switch to fallback provider on failure")


class SessionPromptRequest(BaseModel):
    """Schema for POST /chat/sessions/{id}/prompt"""
    provider: str
    model: Optional[str] = None
    message: str = Field(..., min_length=1)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=32768)
    stream: bool = False
    use_fallback: bool = True


# ───────────────────────────────────────────────────────────────
# STATELESS CHAT  — POST /api/v1/chat
# ───────────────────────────────────────────────────────────────

@router.post("")
async def stateless_chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    One-shot chat completion. No session management needed.

    This is the simplest way to interact with the gateway.
    Send a provider, model, and message — get a unified response back.

    Supports:
      • Dynamic provider switching (just change the 'provider' field)
      • Automatic fallback (if use_fallback=true)
      • Streaming via SSE (if stream=true)
    """
    # Resolve model — use default if not specified
    model = payload.model or get_default_model(payload.provider)
    if not model:
        raise HTTPException(status_code=400, detail=f"No default model found for provider '{payload.provider}'")

    # Validate: accept a virtual model (smart Router) OR a legacy provider/model pair.
    if model not in list_virtual_models() and not resolve_litellm_model(payload.provider, model):
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not available. Check GET /api/v1/providers.",
        )

    messages = [{"role": "user", "content": payload.message}]

    # ── STREAMING PATH ──────────────────────────────────
    if payload.stream:
        async def event_stream():
            collected_content = []
            async for chunk in generate_completion_stream(
                provider=payload.provider,
                model=model,
                messages=messages,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            ):
                if chunk["error"]:
                    yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                    return
                collected_content.append(chunk["content"])
                yield f"data: {json.dumps({'content': chunk['content'], 'done': chunk['done']})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── STANDARD PATH (with optional fallback) ──────────
    if payload.use_fallback:
        result = completion_with_fallback(
            provider=payload.provider,
            model=model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    else:
        result = generate_completion(
            provider=payload.provider,
            model=model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )

    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["error"])

    # Log to analytics
    _log_usage(db, current_user.id, result)

    # Return unified response
    return success_response(
        data=llm_response(
            provider=result["provider"],
            model=result["model"],
            content=result["content"],
            prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"],
            total_tokens=result["total_tokens"],
            cost=result["cost"],
            latency=result["latency"],
            fallback_used=result.get("fallback_used", False),
            original_provider=result.get("original_provider"),
            original_model=result.get("original_model"),
        ),
        message="Chat completion successful",
    )


# ───────────────────────────────────────────────────────────────
# SESSION-BASED CHAT
# ───────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=ChatSessionResponse)
def create_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new conversation session."""
    session = ChatSession(
        id=str(uuid.uuid4()),
        title="New Chat",
        user_id=current_user.id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/sessions", response_model=List[ChatSessionResponse])
def list_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all sessions for the authenticated user, newest first."""
    return (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionDetailResponse)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a session with its full message history."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a session and all its messages (cascade)."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
    return None


class SessionRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
def rename_session(
    session_id: str,
    payload: SessionRenameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename a conversation session."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.title = payload.title
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session


@router.post("/sessions/{session_id}/prompt")
async def session_prompt(
    session_id: str,
    payload: SessionPromptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a prompt within an existing session (preserves conversation history)."""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Resolve model
    model = payload.model or get_default_model(payload.provider)
    if not model:
        raise HTTPException(status_code=400, detail=f"No default model for '{payload.provider}'")

    # Save user message
    user_msg = ChatMessage(session_id=session_id, role="user", content=payload.message)
    db.add(user_msg)
    db.commit()

    # Build conversation history
    past = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    messages = [{"role": m.role, "content": m.content} for m in past]

    # ── Streaming ───────────────────────────────────────
    if payload.stream:
        async def event_stream():
            collected = []
            async for chunk in generate_completion_stream(
                provider=payload.provider,
                model=model,
                messages=messages,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            ):
                if chunk["error"]:
                    yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                    return
                collected.append(chunk["content"])
                yield f"data: {json.dumps({'content': chunk['content'], 'done': chunk['done']})}\n\n"

            # Save assistant message after stream completes
            full_content = "".join(collected)
            if full_content:
                assistant_msg = ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=full_content,
                    provider=payload.provider,
                    model=model,
                )
                db.add(assistant_msg)
                session.updated_at = datetime.now(timezone.utc)
                db.commit()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── Standard (with optional fallback) ───────────────
    if payload.use_fallback:
        result = completion_with_fallback(
            provider=payload.provider,
            model=model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    else:
        result = generate_completion(
            provider=payload.provider,
            model=model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )

    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["error"])

    # Save assistant message
    assistant_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=result["content"],
        provider=result["provider"],
        model=result["model"],
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        total_tokens=result["total_tokens"],
        cost=result["cost"],
        latency=result["latency"],
    )
    db.add(assistant_msg)
    _log_usage(db, current_user.id, result)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(assistant_msg)

    return success_response(
        data=llm_response(
            provider=result["provider"],
            model=result["model"],
            content=result["content"],
            prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"],
            total_tokens=result["total_tokens"],
            cost=result["cost"],
            latency=result["latency"],
            fallback_used=result.get("fallback_used", False),
            original_provider=result.get("original_provider"),
            original_model=result.get("original_model"),
        ),
        message="Chat completion successful",
    )


# ───────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ───────────────────────────────────────────────────────────────

def _log_usage(db: Session, user_id: int, result: dict):
    """Persist a token usage log entry for analytics."""
    log = TokenUsageLog(
        user_id=user_id,
        provider=result["provider"],
        model=result["model"],
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        total_tokens=result["total_tokens"],
        cost=result.get("cost", 0.0),
        latency=result.get("latency", 0.0),
        status_code=200 if result["success"] else 500,
        error_message=result.get("error"),
    )
    db.add(log)
