import type { FileListResponse, FileReadResponse, MemoryResponse, SessionSummary } from './types';

const apiBaseParam = new URLSearchParams(window.location.search).get('apiBase');

export const API_BASE = apiBaseParam || window.funharness?.apiBase || 'http://127.0.0.1:8765';

async function responseError(response: Response) {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as { detail?: string };
    return parsed.detail || text || response.statusText;
  } catch {
    return text || response.statusText;
  }
}

async function post<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  return response.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  return response.json() as Promise<T>;
}

export interface SkillMeta {
  name: string;
  description: string;
  path: string;
}

export const api = {
  info: () => get('/api/info'),
  health: () => get('/api/health'),
  workspace: () => get<FileListResponse>('/api/workspace'),
  tasks: () => get('/api/tasks'),
  runtime: () => get('/api/runtime'),
  schedules: () => get('/api/schedules'),
  sessions: () => get<SessionSummary[]>('/api/sessions'),
  deleteSession: async (session_id: string): Promise<{ ok: boolean }> => {
    const res = await fetch(`${API_BASE}/api/session/${encodeURIComponent(session_id)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await responseError(res));
    return res.json() as Promise<{ ok: boolean }>;
  },
  skills: () => get<SkillMeta[]>('/api/skills'),
  session: (session_id: string) => get(`/api/session/${encodeURIComponent(session_id)}`),
  memory: () => get<MemoryResponse>('/api/memory'),
  chat: (message: string) => post('/api/chat', { message }),
  slash: (command: string) => post('/api/slash', { command }),
  approve: (approval_id: string, approved: boolean, choice: string) =>
    post('/api/approval', { approval_id, approved, choice }),
  interrupt: () => post('/api/interrupt'),
  clear: () => post('/api/clear'),
  mode: (mode: string) => post('/api/mode', { mode }),
  newSession: () => post('/api/session/new'),
  saveSession: () => post('/api/session/save'),
  loadSession: (session_id: string) => post('/api/session/load', { session_id }),
  saveMemory: (content: string) => post('/api/memory', { content }),
  listFiles: (path: string) => post<FileListResponse>('/api/files/list', { path }),
  readFile: (path: string) => post<FileReadResponse>('/api/files/read', { path }),
  runtimeOutput: (runtime_id: string) => post('/api/runtime/output', { runtime_id }),
};
