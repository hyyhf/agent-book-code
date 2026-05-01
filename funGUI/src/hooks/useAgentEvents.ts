import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useRef, useState } from 'react';
import { API_BASE, api } from '../api';
import { appendEvent } from '../lib/events';
import type { AgentInfo, ChatItem, GuiEvent, GuiEventType } from '../types';
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
  'approval_requested',
  'status',
  'system_message',
  'session_loaded',
  'memory_saved',
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
}: {
  clientId: string;
  setInfo: (value: AgentInfo | null) => void;
  setItems: Dispatch<SetStateAction<ChatItem[]>>;
}) {
  const [connection, setConnection] = useState<EventConnection>('connecting');
  const warnedDisconnect = useRef(false);

  useEffect(() => {
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
      if (parsed.type === 'memory_saved') {
        notify.success('Memory saved');
      }
      setItems((prev) => appendEvent(prev, parsed));
    };

    eventTypes.forEach((type) => source.addEventListener(type, handle));

    return () => {
      source.close();
      setConnection('disconnected');
    };
  }, [clientId, setInfo, setItems]);

  return connection;
}
