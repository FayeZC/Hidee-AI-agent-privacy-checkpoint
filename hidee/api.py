"""Agent-facing HTTP API. Each agent authenticates with its own bearer token."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .agents import authenticate
from .engine import ActionError
from .store import ActionRecord

router = APIRouter(prefix="/api")


class FieldIn(BaseModel):
    value: str
    category: str


class SubmitIn(BaseModel):
    kind: str
    recipient: str
    fields: dict[str, FieldIn]
    minimal: Optional[dict[str, FieldIn]] = None
    wait: int = Field(0, ge=0, le=120)


def public(rec: ActionRecord) -> dict:
    out = {"id": rec.id, "status": rec.status, "reason": rec.reason}
    if rec.status in ("allowed", "minimized"):
        out["executed"] = rec.executed
    return out


def require_agent(request: Request, authorization: str = Header(default="")) -> dict:
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    agent = authenticate(request.app.state.store, token)
    if agent is None:
        request.app.state.store.audit("auth_failed", None, {"path": request.url.path}, request.app.state.clock())
        raise HTTPException(401, "A valid agent token is required", headers={"WWW-Authenticate": "Bearer"})
    return agent


def _wait(request: Request, rec: ActionRecord, seconds: int) -> ActionRecord:
    engine = request.app.state.engine
    deadline = time.monotonic() + seconds
    while rec.status == "waiting" and time.monotonic() < deadline:
        time.sleep(0.25)
        engine.expire_waiting()
        rec = engine.get(rec.id)
    return rec


@router.post("/actions", status_code=201)
def submit(body: SubmitIn, request: Request, agent: dict = Depends(require_agent)) -> dict:
    try:
        rec = request.app.state.engine.submit(
            agent["id"], body.kind, body.recipient,
            {k: v.model_dump() for k, v in body.fields.items()},
            {k: v.model_dump() for k, v in body.minimal.items()} if body.minimal else None)
    except ActionError as e:
        raise HTTPException(422, str(e)) from None
    return public(_wait(request, rec, body.wait))


@router.get("/actions/{action_id}")
def get_action(action_id: str, request: Request, wait: int = 0, agent: dict = Depends(require_agent)) -> dict:
    rec = request.app.state.engine.get(action_id)
    if rec is None or rec.agent_id != agent["id"]:
        raise HTTPException(404, "No such action")
    return public(_wait(request, rec, max(0, min(wait, 120))))
