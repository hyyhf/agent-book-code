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


class ModelProfileRequest(BaseModel):
    id: str = ""
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    enabled: bool = True


class ModelProfilesSaveRequest(BaseModel):
    profiles: list[ModelProfileRequest] = Field(default_factory=list)
    default_profile_id: str = "__env__"


class ModelProfileSelectRequest(BaseModel):
    profile_id: str


class PathRequest(BaseModel):
    path: str = "."


class RuntimeOutputRequest(BaseModel):
    runtime_id: str


class SessionLoadRequest(BaseModel):
    session_id: str


class MemoryUpdateRequest(BaseModel):
    content: str
