# FunHarness Local Agent GUI Schedule

## Goal

Build a local desktop Agent GUI in `funGUI/` while keeping the existing FunHarness TUI fully usable. The GUI must reuse `FunHarnessAgent` as the only agent kernel and expose its existing callback surface through FastAPI, SSE, and HTTP POST controls.

## Architecture

- Backend: `funGUI/backend/`
  - FastAPI app, runnable with `uv run python -m funGUI.backend`.
  - Process working directory defaults to repository `defaultspace/`, matching `fh`.
  - SSE is the primary event channel: agent callbacks publish into `asyncio.Queue` instances.
  - Control commands use normal HTTP POST endpoints.
  - The agent runs in a background thread so the HTTP server stays responsive.
- Frontend: `funGUI/`
  - Electron + React + TypeScript + Vite.
  - UI library: `@arco-design/web-react`.
  - Icons: `@icon-park/react`.
  - Electron starts and stops the FastAPI backend for the local desktop app.
- Core rule:
  - Do not move or rewrite the Textual TUI.
  - Any shared behavior must be added through compatible adapter methods or new GUI-only modules.

## Backend Interfaces

### SSE

`GET /api/events?client_id=<id>`

Events:

- `connected`
- `run_started`
- `user_message`
- `assistant_delta`
- `reasoning_start`
- `reasoning_delta`
- `reasoning_done`
- `tool_gen_delta`
- `tool_call`
- `tool_result`
- `approval_requested`
- `status`
- `system_message`
- `info_update`
- `run_finished`
- `error`

### POST Controls

- `POST /api/chat` with `{ "message": "..." }`
- `POST /api/slash` with `{ "command": "/help" }`
- `POST /api/approval` with `{ "approval_id": "...", "approved": true, "choice": "once" }`
- `POST /api/interrupt`
- `POST /api/clear`
- `POST /api/mode` with `{ "mode": "suggest" }`
- `POST /api/session/new`
- `POST /api/session/save`
- `POST /api/files/list` with `{ "path": "." }`
- `POST /api/files/read` with `{ "path": "README.md" }`
- `POST /api/runtime/output` with `{ "runtime_id": "run_xxxxxxxx" }`

### GET Queries

- `GET /api/health`
- `GET /api/info`
- `GET /api/workspace`
- `GET /api/sessions`
- `GET /api/tasks`
- `GET /api/schedules`
- `GET /api/runtime`

## UI Schedule

1. Shell layout
   - AionUi-like left rail, top title bar, central work area, right workspace panel.
   - First screen is the usable agent composer, not a landing page.
2. Chat stream
   - User message, assistant streaming text, reasoning block, tool call/result cards, status messages.
   - Approval requests render as modal dialogs with `Allow Once`, `Always Allow`, and `Deny`.
3. Workspace
   - File tree rooted at `defaultspace/`.
   - Preview tabs for text, Markdown, JSON/code, images, PDF, and unknown file fallback.
4. Agent control
   - Permission mode segmented control.
   - Interrupt button.
   - Slash command menu for all existing TUI commands.
5. Panels
   - Tasks, runtime, schedules, sessions, trace/logs/dashboard.

## Verification

- `uv run python -m compileall funGUI/backend`
- `uv run python -m funGUI.backend --host 127.0.0.1 --port 8765` starts and serves `/api/health`.
- `uv run fh` remains the TUI entrypoint.
- `npm install` then `npm run typecheck` and `npm run build` work inside `funGUI/`.
- Manual smoke:
  - Open GUI.
  - Send `/help`.
  - Send a normal prompt.
  - Switch permission mode.
  - Trigger an approval-requiring tool and approve/deny it.
  - Browse workspace files and preview a generated file.

