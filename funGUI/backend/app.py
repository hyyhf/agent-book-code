from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .events import EventBus
from .schemas import (
    ApprovalRequest,
    ChatRequest,
    MemoryUpdateRequest,
    ModeRequest,
    ModelProfileRequest,
    ModelProfileSelectRequest,
    ModelProfilesSaveRequest,
    PathRequest,
    RuntimeOutputRequest,
    SessionLoadRequest,
    SlashRequest,
)
from .service import AgentService


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    root = _repo_root()
    workspace = root / "defaultspace"
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    bus = EventBus()
    service = AgentService(bus=bus, workspace=workspace.resolve(), loop=asyncio.get_running_loop())
    app.state.root = root
    app.state.workspace = workspace.resolve()
    app.state.bus = bus
    app.state.agent_service = service
    await bus.publish("info_update", service.snapshot())
    try:
        yield
    finally:
        service.agent.scheduler.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="FunHarness GUI Backend", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "app://funharness",
        ],
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "workspace": str(app.state.workspace)}

    @app.get("/api/events")
    async def events(request: Request, client_id: str = "default") -> StreamingResponse:
        bus: EventBus = app.state.bus
        queue = await bus.connect(client_id)

        async def stream() -> AsyncIterator[str]:
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield event.to_sse()
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                await bus.disconnect(client_id)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/info")
    async def info() -> dict:
        service: AgentService = app.state.agent_service
        return service.snapshot()

    @app.get("/api/workspace")
    async def workspace() -> dict:
        service: AgentService = app.state.agent_service
        return service.list_files(".")

    @app.delete("/api/session/{session_id}")
    async def delete_session(session_id: str) -> dict:
        service: AgentService = app.state.agent_service
        return await service.delete_session(session_id)

    @app.get("/api/skills")
    async def skills() -> list[dict]:
        service: AgentService = app.state.agent_service
        return service.skills()

    @app.get("/api/sessions")
    async def sessions() -> list[dict]:
        service: AgentService = app.state.agent_service
        return service.sessions()

    @app.get("/api/session/{session_id}")
    async def session(session_id: str) -> dict:
        service: AgentService = app.state.agent_service
        return service.get_session(session_id)

    @app.get("/api/memory")
    async def memory() -> dict:
        service: AgentService = app.state.agent_service
        return service.memory()

    @app.get("/api/tasks")
    async def tasks() -> dict:
        service: AgentService = app.state.agent_service
        return service.tasks()

    @app.get("/api/team")
    async def team() -> dict:
        service: AgentService = app.state.agent_service
        return service.team()

    @app.get("/api/team/{name}/inbox")
    async def team_inbox(name: str) -> dict:
        service: AgentService = app.state.agent_service
        return service.team_inbox(name)

    @app.get("/api/schedules")
    async def schedules() -> list[dict]:
        service: AgentService = app.state.agent_service
        return service.schedules()

    @app.get("/api/runtime")
    async def runtime() -> list[dict]:
        service: AgentService = app.state.agent_service
        return service.runtime()

    @app.post("/api/chat")
    async def chat(body: ChatRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.chat(body.message)

    @app.post("/api/slash")
    async def slash(body: SlashRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.slash(body.command)

    @app.get("/api/attachments")
    async def attachments() -> list[dict]:
        service: AgentService = app.state.agent_service
        return service.attachments()

    @app.post("/api/attachments/upload")
    async def upload_attachments(files: list[UploadFile] = File(...)) -> dict:
        service: AgentService = app.state.agent_service
        return await service.upload_attachments(files)

    @app.delete("/api/attachments/{attachment_id}")
    async def detach_attachment(attachment_id: str) -> dict:
        service: AgentService = app.state.agent_service
        return await service.detach_attachment(attachment_id)

    @app.delete("/api/attachments")
    async def detach_all_attachments() -> dict:
        service: AgentService = app.state.agent_service
        return await service.detach_all_attachments()

    @app.post("/api/approval")
    async def approval(body: ApprovalRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.approve(body.approval_id, body.approved, body.choice)

    @app.post("/api/interrupt")
    async def interrupt() -> dict:
        service: AgentService = app.state.agent_service
        return await service.interrupt()

    @app.post("/api/clear")
    async def clear() -> dict:
        service: AgentService = app.state.agent_service
        return await service.clear()

    @app.post("/api/mode")
    async def mode(body: ModeRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.set_mode(body.mode)

    @app.get("/api/model-profiles")
    async def model_profiles() -> dict:
        service: AgentService = app.state.agent_service
        return service.model_profiles()

    @app.put("/api/model-profiles")
    async def save_model_profiles(body: ModelProfilesSaveRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.save_model_profiles(
            [item.model_dump() for item in body.profiles],
            body.default_profile_id,
        )

    @app.post("/api/model-profiles/select")
    async def select_model_profile(body: ModelProfileSelectRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.set_model_profile(body.profile_id)

    @app.post("/api/model-profiles/test")
    async def test_model_profile(body: ModelProfileRequest) -> dict:
        service: AgentService = app.state.agent_service
        return service.test_model_profile(body.model_dump())

    @app.post("/api/session/new")
    async def new_session() -> dict:
        service: AgentService = app.state.agent_service
        return await service.new_session()

    @app.post("/api/session/save")
    async def save_session() -> dict:
        service: AgentService = app.state.agent_service
        return await service.save_session()

    @app.post("/api/session/load")
    async def load_session(body: SessionLoadRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.load_session(body.session_id)

    @app.post("/api/memory")
    async def update_memory(body: MemoryUpdateRequest) -> dict:
        service: AgentService = app.state.agent_service
        return await service.update_memory(body.content)

    @app.post("/api/files/list")
    async def list_files(body: PathRequest) -> dict:
        service: AgentService = app.state.agent_service
        return service.list_files(body.path)

    @app.post("/api/files/read")
    async def read_file(body: PathRequest) -> dict:
        service: AgentService = app.state.agent_service
        return service.read_file(body.path)

    @app.get("/api/files/raw")
    async def raw_file(path: str) -> FileResponse:
        service: AgentService = app.state.agent_service
        return FileResponse(service.raw_file(path))

    @app.post("/api/runtime/output")
    async def runtime_output(body: RuntimeOutputRequest) -> dict:
        service: AgentService = app.state.agent_service
        return service.runtime_output(body.runtime_id)

    return app
