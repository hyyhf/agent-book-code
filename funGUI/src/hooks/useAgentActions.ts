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
  refreshAttachments,
  hasAttachments,
  planMode,
}: {
  input: string;
  setInput: (value: string) => void;
  setItems: Dispatch<SetStateAction<ChatItem[]>>;
  setPanel: (panel: PanelKey | null) => void;
  setInfo: (value: AgentInfo | null) => void;
  resetRuntimeOutputs: () => void;
  refreshAttachments: () => Promise<void>;
  hasAttachments: boolean;
  planMode: boolean;
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
      const typedText = value.trim();
      let text = typedText;
      if (!text && hasAttachments) {
        text = '请阅读并总结我上传的附件。';
      }
      if (!text) return;
      if (planMode && typedText && !text.startsWith('/')) {
        text = `/plan ${text}`;
      }
      setInput('');
      try {
        if (text.startsWith('/')) {
          await api.slash(text);
        } else {
          await api.chat(text);
        }
      } catch (error) {
        setInput(typedText || text);
        pushError('Failed to send message', error);
      }
    },
    [hasAttachments, input, planMode, pushError, setInput],
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
        await refreshAttachments();
        setPanel(null);
        notify.success('Session loaded');
      } catch (error) {
        pushError('Failed to load session', error);
        throw error;
      }
    },
    [pushError, refreshAttachments, setPanel],
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
      await refreshAttachments();
      setInfo((await api.info()) as AgentInfo);
      notify.success('刚才的会话已保存');
    } catch (error) {
      pushError('Failed to start a new session', error);
    }
  }, [pushError, refreshAttachments, resetRuntimeOutputs, setInfo, setItems, setPanel]);

  return {
    sendMessage,
    resolveApproval,
    loadSession,
    saveSession,
    newSession,
  };
}
