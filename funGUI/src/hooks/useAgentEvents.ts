import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useRef, useState } from 'react';
import { API_BASE, api } from '../api';
import { appendEvent } from '../lib/events';
import type { AgentInfo, AttachmentRecord, ChatItem, GuiEvent, GuiEventType } from '../types';
import { notify } from '../utils/notify';

const eventTypes: GuiEventType[] = [
  'connected',
  'run_started',
  'user_message',
  'assistant_delta',
  'reasoning_start',
  'reasoning_delta',
  'reasoning_done',
  'tool_gen_delta',
  'tool_call',
  'tool_result',
  'plan_delta',
  'task_completed',
  'approval_requested',
  'status',
  'system_message',
  'session_loaded',
  'memory_saved',
  'attachments_updated',
  'tasks_updated',
  'info_update',
  'run_finished',
  'error',
];

export type EventConnection = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export function useAgentEvents({
  clientId,
  setInfo,
  setItems,
  setAttachments,
}: {
  clientId: string;
  setInfo: Dispatch<SetStateAction<AgentInfo | null>>;
  setItems: Dispatch<SetStateAction<ChatItem[]>>;
  setAttachments: Dispatch<SetStateAction<AttachmentRecord[]>>;
}) {
  const [connection, setConnection] = useState<EventConnection>('connecting');
  const warnedDisconnect = useRef(false);
  const pendingEvents = useRef<GuiEvent[]>([]);
  const flushFrame = useRef<number | null>(null);

  useEffect(() => {
    const flushItems = () => {
      flushFrame.current = null;
      const events = pendingEvents.current;
      pendingEvents.current = [];
      if (!events.length) return;
      setItems((prev) => events.reduce((nextItems, nextEvent) => appendEvent(nextItems, nextEvent), prev));
    };

    const queueItemEvent = (event: GuiEvent) => {
      pendingEvents.current.push(event);
      if (flushFrame.current !== null) return;
      flushFrame.current = window.requestAnimationFrame(flushItems);
    };

    void api.info()
      .then((data) => setInfo(data as AgentInfo))
      .catch((error) => {
        setConnection('disconnected');
        notify.error(`Failed to load agent info: ${errorText(error)}`);
      });

    const source = new EventSource(`${API_BASE}/api/events?client_id=${encodeURIComponent(clientId)}`);

    source.onopen = () => {
      setConnection('connected');
      warnedDisconnect.current = false;
    };

    source.onerror = () => {
      setConnection('reconnecting');
      if (!warnedDisconnect.current) {
        warnedDisconnect.current = true;
        notify.warning('Event stream disconnected. Reconnecting...');
      }
    };

    const handle = (event: MessageEvent) => {
      let parsed: GuiEvent;
      try {
        parsed = JSON.parse(event.data) as GuiEvent;
      } catch {
        notify.error('Received an invalid event from the backend.');
        return;
      }

      if (parsed.type === 'info_update') {
        setInfo(parsed.payload as unknown as AgentInfo);
      }
      if (parsed.type === 'run_started') {
        setInfo((prev) => (prev ? { ...prev, busy: true } : prev));
      }
      if (parsed.type === 'run_finished') {
        setInfo((prev) => (prev ? { ...prev, busy: false } : prev));
      }
      if (parsed.type === 'memory_saved') {
        notify.success('Memory saved');
      }
      if (parsed.type === 'attachments_updated') {
        setAttachments((parsed.payload.attachments || []) as unknown as AttachmentRecord[]);
      }
      if (parsed.type === 'session_loaded') {
        setAttachments((parsed.payload.attachments || []) as unknown as AttachmentRecord[]);
      }
      queueItemEvent(parsed);
    };

    eventTypes.forEach((type) => source.addEventListener(type, handle));

    return () => {
      source.close();
      if (flushFrame.current !== null) {
        window.cancelAnimationFrame(flushFrame.current);
        flushFrame.current = null;
      }
      pendingEvents.current = [];
      setConnection('disconnected');
    };
  }, [clientId, setAttachments, setInfo, setItems]);

  return connection;
}
