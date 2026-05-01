export type GuiEventType =
  | 'connected'
  | 'run_started'
  | 'user_message'
  | 'assistant_delta'
  | 'reasoning_start'
  | 'reasoning_delta'
  | 'reasoning_done'
  | 'tool_gen_delta'
  | 'tool_call'
  | 'tool_result'
  | 'approval_requested'
  | 'status'
  | 'system_message'
  | 'session_loaded'
  | 'memory_saved'
  | 'info_update'
  | 'run_finished'
  | 'error';

export interface GuiEvent {
  type: GuiEventType;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface AgentInfo {
  mode: string;
  tools: number;
  trace_id: string;
  messages: number;
  tokens: number;
  cost: string;
  tasks_ready: number;
  teammates: number;
  runtime_tasks: number;
  schedules: number;
  busy: boolean;
  workspace: string;
  cwd: string;
  model: string;
}

export type PanelKey = 'search' | 'history' | 'agents' | 'dashboard' | 'skills' | 'memory' | 'settings';

export type ChatItem =
  | { id: string; type: 'user'; content: string }
  | { id: string; type: 'assistant'; content: string; streaming: boolean }
  | { id: string; type: 'reasoning'; content: string; done: boolean }
  | { id: string; type: 'tool_gen'; name: string; content: string }
  | { id: string; type: 'tool'; name: string; risk: string; preview: unknown; result?: string; hookFeedback?: string; approval?: ApprovalState }
  | { id: string; type: 'system' | 'status' | 'error'; content: string };

export interface ApprovalState {
  approvalId: string;
  toolName: string;
  reason: string;
  arguments: unknown;
}

export interface FileEntry {
  name: string;
  path: string;
  kind: 'directory' | 'file';
  size: number;
  modified_at: number;
}

export interface FileListResponse {
  path: string;
  entries: FileEntry[];
}

export interface FileReadResponse {
  path: string;
  kind: 'text' | 'binary';
  extension: string;
  size: number;
  content?: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  parent_id?: string | null;
}

export interface MemoryResponse {
  path: string;
  content: string;
}

export interface TasksResponse {
  summary: string;
  tasks: Array<Record<string, unknown>>;
}

export interface RuntimeTask {
  runtime_id: string;
  kind: string;
  description: string;
  status: string;
  created_at: number;
  started_at: number;
  finished_at: number;
  result_preview: string;
  output_file: string;
  error: string;
}

export interface DashboardData {
  tasks?: TasksResponse;
  runtime?: RuntimeTask[];
  schedules?: Array<Record<string, unknown>>;
}
