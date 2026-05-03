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
  | 'plan_delta'
  | 'task_completed'
  | 'approval_requested'
  | 'status'
  | 'system_message'
  | 'session_loaded'
  | 'memory_saved'
  | 'attachments_updated'
  | 'tasks_updated'
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
  model_profile_id: string;
  model_profile_name: string;
  model_profiles: ModelProfile[];
}

export interface ModelProfile {
  id: string;
  name: string;
  base_url: string;
  model: string;
  enabled: boolean;
  source: 'env' | 'user';
  has_api_key: boolean;
  api_key_masked: string;
}

export interface ModelProfilesResponse {
  config_path: string;
  default_profile_id: string;
  active_profile_id: string;
  profiles: ModelProfile[];
}

export interface ModelProfileDraft {
  id: string;
  name: string;
  base_url: string;
  api_key?: string;
  model: string;
  enabled: boolean;
}

export interface ModelProfileTestResponse {
  ok: boolean;
  message: string;
  latency_ms: number;
  model?: string;
  preview?: string;
}

export type PanelKey = 'search' | 'history' | 'agents' | 'dashboard' | 'skills' | 'memory' | 'settings';

export type ChatItem =
  | { id: string; type: 'user'; content: string }
  | { id: string; type: 'assistant'; content: string; streaming: boolean }
  | { id: string; type: 'reasoning'; content: string; done: boolean }
  | { id: string; type: 'plan_draft'; content: string; done: boolean }
  | { id: string; type: 'task_completion'; task: TaskRecord | null; progress: TaskProgress; content: string }
  | { id: string; type: 'tool_gen'; index: number; name: string; content: string; done: boolean }
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

export interface AttachmentRecord {
  id: string;
  original_name: string;
  stored_path: string;
  extension: string;
  mime_type: string;
  size: number;
  added_at: string;
  parse_status: string;
  preview: string;
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
  tasks: TaskRecord[];
}

export interface TaskRecord {
  task_id: string;
  title: string;
  description: string;
  verify: string;
  depends_on: string[];
  blocks: string[];
  owner: string;
  status: 'pending' | 'in_progress' | 'done' | 'failed' | 'skipped';
  artifacts: string[];
  notes: string;
  error: string;
  created_at: string;
  started_at: string;
  finished_at: string;
}

export interface TaskProgress {
  done: number;
  total: number;
  percent: number;
}

export interface TeamMember {
  name: string;
  role: string;
  instructions: string;
  status: string;
  created_at: number;
  last_active_at: number;
  inbox_count: number;
}

export interface TeamInboxItem {
  type: string;
  from: string;
  to: string;
  content: string;
  timestamp: number;
}

export interface TeamResponse {
  summary: string;
  members: TeamMember[];
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

export interface ScheduleRecord {
  schedule_id: string;
  name: string;
  when: string;
  prompt: string;
  recurring: boolean;
  enabled: boolean;
  created_at: number;
  next_fire_at: number;
  last_fired_at: number;
  last_fired_key: string;
  last_runtime_id: string;
  last_run_error: string;
}

export interface DashboardData {
  team?: TeamResponse;
  tasks?: TasksResponse;
  runtime?: RuntimeTask[];
  schedules?: ScheduleRecord[];
}
