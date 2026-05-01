# FunHarness GUI

`funGUI/` is a local desktop GUI for the existing FunHarness agent core. It keeps the Textual TUI intact and connects to `FunHarnessAgent` through a FastAPI backend.

## Backend

```powershell
uv run python -m funGUI.backend --host 127.0.0.1 --port 8765
```

Health check:

```powershell
curl http://127.0.0.1:8765/api/health
```

The backend uses `defaultspace/` as its working directory, matching the `fh` command.

## Frontend

```powershell
cd funGUI
npm install
npm run dev
```

Electron starts the backend automatically unless `FUNGUI_EXTERNAL_BACKEND=1` is set.

## Useful Scripts

- `npm run dev`: run Vite and Electron.
- `npm run typecheck`: TypeScript check.
- `npm run build`: renderer production build.

## Event Flow

- SSE: `GET /api/events?client_id=<id>`
- Control: `POST /api/chat`, `/api/slash`, `/api/approval`, `/api/interrupt`, `/api/mode`
- Workspace: `GET /api/workspace`, `POST /api/files/list`, `POST /api/files/read`, `GET /api/files/raw?path=...`
