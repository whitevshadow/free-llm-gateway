"""
Playground support — saved presets and prompt improvement.

The Playground's actual conversation goes through /v1/chat/completions like any
other client; nothing here proxies chat. What it needs beyond that is somewhere
to keep prompt presets, and one convenience action that itself calls a model.

WHY PRESETS LIVE IN user_settings
    They are per-user UI state with no schema worth enforcing — a name, a system
    prompt, a model, some sampling parameters. Giving them a table would mean a
    migration every time the Playground adds a field, and nothing in the gateway
    reads them but the Playground.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.gateway_auth import current_user
from app.api.settings import read_settings, write_setting
from app.core.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Playground"])

PRESETS_KEY = "playgroundPresets"


class Preset(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., max_length=120)
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # Anything else the Playground wants to round-trip. Kept opaque on purpose —
    # the gateway does not interpret these, so it should not constrain them.
    extra: Dict[str, Any] = Field(default_factory=dict)


def _load(db: Session, user_id: int) -> List[dict]:
    stored = read_settings(db, user_id).get(PRESETS_KEY)
    return stored if isinstance(stored, list) else []


@router.get("/v1/me/playground/presets")
async def list_presets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"presets": _load(db, user.id)}


@router.post("/v1/me/playground/presets")
async def save_preset(
    payload: Preset, user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """
    Create or update a preset.

    The id is generated server-side when absent so two tabs saving at once cannot
    collide on a client-chosen name.
    """
    presets = _load(db, user.id)
    record = payload.model_dump()
    record["id"] = record["id"] or uuid.uuid4().hex[:12]

    replaced = False
    for index, existing in enumerate(presets):
        if existing.get("id") == record["id"]:
            presets[index] = record
            replaced = True
            break
    if not replaced:
        presets.append(record)

    write_setting(db, user.id, PRESETS_KEY, presets)
    db.commit()
    return record


@router.delete("/v1/me/playground/presets/{preset_id}")
async def delete_preset(
    preset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db),
):
    presets = _load(db, user.id)
    remaining = [p for p in presets if p.get("id") != preset_id]
    if len(remaining) == len(presets):
        raise HTTPException(status_code=404, detail="No such preset.")
    write_setting(db, user.id, PRESETS_KEY, remaining)
    db.commit()
    return {"success": True, "id": preset_id}


class ImprovePromptRequest(BaseModel):
    prompt: str
    model: Optional[str] = Field(
        None, description="Model to rewrite with. Defaults to the gateway's `auto`."
    )


@router.post("/v1/me/playground/improve-prompt")
async def improve_prompt(
    payload: ImprovePromptRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """
    Rewrite a prompt to be clearer, using the gateway's own routing.

    Deliberately routed through the user's own Router rather than a hardcoded
    model: this gateway's premise is that you use the providers YOU hold keys
    for, and a feature that quietly required a specific vendor would break that.

    Returns the original alongside the rewrite. The caller decides whether to
    accept it — silently replacing what someone typed is the wrong default.
    """
    from app.core import llm_router

    if not payload.prompt.strip():
        raise HTTPException(status_code=400, detail="Nothing to improve.")

    router_obj = llm_router.get_router(user.id, db)
    names = llm_router.list_models(user.id, db)

    model = payload.model or (names[0] if names else None)
    if not model:
        raise HTTPException(
            status_code=400,
            detail="No callable model. Add a provider key first.",
        )

    instruction = (
        "Rewrite the user's prompt so it is clearer and more specific, keeping "
        "their intent and voice. Do not answer it. Return only the rewritten "
        "prompt, with no preamble or quotation marks."
    )

    try:
        response = await router_obj.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": payload.prompt},
            ],
            max_tokens=600,
            temperature=0.4,
        )
        raw = response.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("improve-prompt failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"The model call failed: {exc}")

    return {
        "original": payload.prompt,
        "improved": raw.strip(),
        "model": model,
    }
