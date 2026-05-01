from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class SlashRequest(BaseModel):
    command: str = Field(min_length=1)


class ApprovalRequest(BaseModel):
    approval_id: str
    approved: bool
    choice: str = "once"


class ModeRequest(BaseModel):
    mode: str


class PathRequest(BaseModel):
    path: str = "."


class RuntimeOutputRequest(BaseModel):
    runtime_id: str


class SessionLoadRequest(BaseModel):
    session_id: str


class MemoryUpdateRequest(BaseModel):
    content: str
