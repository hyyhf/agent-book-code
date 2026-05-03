import type { ApprovalState, ChatItem, GuiEvent } from '../types';
import { nextId } from '../utils/ids';

function sessionItems(messages: Array<Record<string, unknown>>): ChatItem[] {
  return messages.map((message) => {
    const type = String(message.type || 'system');
    if (type === 'user') return { id: nextId('user'), type: 'user', content: String(message.content || '') };
    if (type === 'assistant') {
      return {
        id: nextId('assistant'),
        type: 'assistant',
        content: String(message.content || ''),
        streaming: Boolean(message.streaming),
      };
    }
    if (type === 'reasoning') {
      return {
        id: nextId('thinking'),
        type: 'reasoning',
        content: String(message.content || ''),
        done: Boolean(message.done),
      };
    }
    return { id: nextId('system'), type: 'system', content: String(message.content || '') };
  });
}

function completeLatestToolGeneration(items: ChatItem[], name: string): ChatItem[] {
  const copy = [...items];
  for (let index = copy.length - 1; index >= 0; index -= 1) {
    const item = copy[index];
    if (item.type === 'tool_gen' && item.name === name && !item.done) {
      copy[index] = { ...item, done: true };
      break;
    }
  }
  return copy;
}

export function appendEvent(items: ChatItem[], event: GuiEvent): ChatItem[] {
  const payload = event.payload;
  if (event.type === 'session_loaded') {
    return sessionItems((payload.messages as Array<Record<string, unknown>>) || []);
  }
  if (event.type === 'user_message') {
    return [...items, { id: nextId('user'), type: 'user', content: String(payload.content || '') }];
  }
  if (event.type === 'assistant_delta') {
    const token = String(payload.token || '');
    const copy = [...items];
    const last = copy[copy.length - 1];
    if (last?.type === 'assistant' && last.streaming) {
      copy[copy.length - 1] = { ...last, content: last.content + token };
      return copy;
    }
    return [...items, { id: nextId('assistant'), type: 'assistant', content: token, streaming: true }];
  }
  if (event.type === 'reasoning_start') {
    return [...items, { id: nextId('thinking'), type: 'reasoning', content: '', done: false }];
  }
  if (event.type === 'reasoning_delta') {
    const token = String(payload.token || '');
    const copy = [...items];
    for (let index = copy.length - 1; index >= 0; index -= 1) {
      const item = copy[index];
      if (item.type === 'reasoning' && !item.done) {
        copy[index] = { ...item, content: item.content + token };
        return copy;
      }
    }
    return [...items, { id: nextId('thinking'), type: 'reasoning', content: token, done: false }];
  }
  if (event.type === 'reasoning_done') {
    return items.map((item) => (item.type === 'reasoning' ? { ...item, done: true } : item));
  }
  if (event.type === 'plan_delta') {
    const token = String(payload.token || '');
    const copy = [...items];
    for (let index = copy.length - 1; index >= 0; index -= 1) {
      const item = copy[index];
      if (item.type === 'plan_draft' && !item.done) {
        copy[index] = { ...item, content: item.content + token };
        return copy;
      }
    }
    return [...items, { id: nextId('plan'), type: 'plan_draft', content: token, done: false }];
  }
  if (event.type === 'task_completed') {
    const progress = (payload.progress || {}) as Record<string, unknown>;
    return [
      ...items,
      {
        id: nextId('task_done'),
        type: 'task_completion',
        task: (payload.task || null) as never,
        progress: {
          done: Number(progress.done || 0),
          total: Number(progress.total || 0),
          percent: Number(progress.percent || 0),
        },
        content: String(payload.message || ''),
      },
    ];
  }
  if (event.type === 'tool_gen_delta') {
    const callIndex = Number(payload.index || 0);
    const name = String(payload.name || 'tool');
    const chunk = String(payload.chunk || '');
    const copy = [...items];
    const last = copy[copy.length - 1];
    if (last?.type === 'tool_gen' && last.name === name && last.index === callIndex) {
      copy[copy.length - 1] = { ...last, content: last.content + chunk };
      return copy;
    }
    return [...items, { id: nextId('tool_gen'), type: 'tool_gen', index: callIndex, name, content: chunk, done: false }];
  }
  if (event.type === 'tool_call') {
    const name = String(payload.name || '');
    return [
      ...completeLatestToolGeneration(items, name),
      {
        id: nextId('tool'),
        type: 'tool',
        name,
        risk: String(payload.risk || 'execute'),
        preview: payload.preview,
      },
    ];
  }
  if (event.type === 'approval_requested') {
    const approval: ApprovalState = {
      approvalId: String(payload.approval_id || ''),
      toolName: String(payload.tool_name || ''),
      reason: String(payload.reason || ''),
      arguments: payload.arguments,
    };
    const copy = completeLatestToolGeneration(items, approval.toolName);
    for (let index = copy.length - 1; index >= 0; index -= 1) {
      const item = copy[index];
      if (item.type === 'tool' && item.name === approval.toolName && !item.result) {
        copy[index] = { ...item, approval };
        return copy;
      }
    }
    return [
      ...items,
      {
        id: nextId('tool'),
        type: 'tool',
        name: approval.toolName,
        risk: 'write',
        preview: approval.arguments,
        approval,
      },
    ];
  }
  if (event.type === 'tool_result') {
    const copy = [...items];
    const name = String(payload.name || '');
    for (let index = copy.length - 1; index >= 0; index -= 1) {
      const item = copy[index];
      if (item.type === 'tool' && item.name === name && !item.result) {
        copy[index] = {
          ...item,
          result: String(payload.result || ''),
          hookFeedback: String(payload.hook_feedback || ''),
          approval: undefined,
        };
        return copy;
      }
    }
    return [
      ...items,
      {
        id: nextId('tool'),
        type: 'tool',
        name,
        risk: 'execute',
        preview: '',
        result: String(payload.result || ''),
        hookFeedback: String(payload.hook_feedback || ''),
      },
    ];
  }
  if (event.type === 'system_message') {
    return [...items, { id: nextId('system'), type: 'system', content: String(payload.content || '') }];
  }
  if (event.type === 'status') {
    return [...items, { id: nextId('status'), type: 'status', content: String(payload.message || '') }];
  }
  if (event.type === 'memory_saved') {
    return [...items, { id: nextId('status'), type: 'status', content: 'Memory saved and system prompt refreshed.' }];
  }
  if (event.type === 'error') {
    return [...items, { id: nextId('error'), type: 'error', content: String(payload.message || '') }];
  }
  if (event.type === 'run_finished') {
    return items.map((item) => {
      if (item.type === 'assistant') return { ...item, streaming: false };
      if (item.type === 'plan_draft') return { ...item, done: true };
      if (item.type === 'tool_gen') return { ...item, done: true };
      return item;
    });
  }
  return items;
}
