import type { Dispatch, SetStateAction } from 'react';
import { useCallback } from 'react';
import { api } from '../api';
import type { AgentInfo, ApprovalState, ChatItem, PanelKey } from '../types';
import { nextId } from '../utils/ids';
import { notify } from '../utils/notify';

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export function useAgentActions({
  input,
  setInput,
  setItems,
  setPanel,
  setInfo,
  resetRuntimeOutputs,
}: {
  input: string;
  setInput: (value: string) => void;
  setItems: Dispatch<SetStateAction<ChatItem[]>>;
  setPanel: (panel: PanelKey | null) => void;
  setInfo: (value: AgentInfo | null) => void;
  resetRuntimeOutputs: () => void;
}) {
  const pushError = useCallback(
    (prefix: string, error: unknown) => {
      const message = `${prefix}: ${errorText(error)}`;
      notify.error(message);
      setItems((prev) => [...prev, { id: nextId('error'), type: 'error', content: message }]);
    },
    [setItems],
  );

  const sendMessage = useCallback(
    async (value = input) => {
      const text = value.trim();
      if (!text) return;
      setInput('');
      try {
        if (text.startsWith('/')) {
          await api.slash(text);
        } else {
          await api.chat(text);
        }
      } catch (error) {
        setInput(text);
        pushError('Failed to send message', error);
      }
    },
    [input, pushError, setInput],
  );

  const resolveApproval = useCallback(
    async (approval: ApprovalState, approved: boolean, choice: string) => {
      try {
        await api.approve(approval.approvalId, approved, choice);
      } catch (error) {
        pushError('Failed to resolve approval', error);
      }
    },
    [pushError],
  );

  const loadSession = useCallback(
    async (sessionId: string) => {
      try {
        await api.loadSession(sessionId);
        setPanel(null);
        notify.success('Session loaded');
      } catch (error) {
        pushError('Failed to load session', error);
        throw error;
      }
    },
    [pushError, setPanel],
  );

  const saveSession = useCallback(async () => {
    try {
      await api.saveSession();
      notify.success('Session saved');
    } catch (error) {
      pushError('Failed to save session', error);
    }
  }, [pushError]);

  const newSession = useCallback(async () => {
    try {
      await api.newSession();
      setItems([]);
      setPanel(null);
      resetRuntimeOutputs();
      setInfo((await api.info()) as AgentInfo);
    } catch (error) {
      pushError('Failed to start a new session', error);
    }
  }, [pushError, resetRuntimeOutputs, setInfo, setItems, setPanel]);

  return {
    sendMessage,
    resolveApproval,
    loadSession,
    saveSession,
    newSession,
  };
}
