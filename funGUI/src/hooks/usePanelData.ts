import { useCallback, useState } from 'react';
import { api } from '../api';
import type { DashboardData, MemoryResponse, PanelKey, SessionSummary } from '../types';
import { notify } from '../utils/notify';

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export function usePanelData(panel: PanelKey | null) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [memory, setMemory] = useState<MemoryResponse | null>(null);
  const [memoryDraft, setMemoryDraft] = useState('');
  const [panelData, setPanelData] = useState<DashboardData>({});
  const [runtimeOutputs, setRuntimeOutputs] = useState<Record<string, string>>({});
  const [panelQuery, setPanelQuery] = useState('');

  const loadPanelData = useCallback(
    async (nextPanel = panel) => {
      try {
        if (nextPanel === 'history') {
          setSessions(await api.sessions());
        }
        if (nextPanel === 'memory') {
          const data = await api.memory();
          setMemory(data);
          setMemoryDraft(data.content);
        }
        if (nextPanel === 'dashboard') {
          const [tasks, runtime, schedules] = await Promise.all([api.tasks(), api.runtime(), api.schedules()]);
          setPanelData({ tasks, runtime, schedules } as DashboardData);
        }
      } catch (error) {
        notify.error(`Failed to load panel data: ${errorText(error)}`);
      }
    },
    [panel],
  );

  const saveMemory = useCallback(async () => {
    try {
      await api.saveMemory(memoryDraft);
      setMemory(await api.memory());
      notify.success('Memory saved and refreshed');
    } catch (error) {
      notify.error(`Failed to save memory: ${errorText(error)}`);
    }
  }, [memoryDraft]);

  const loadRuntimeOutput = useCallback(async (runtimeId: string) => {
    try {
      const data = (await api.runtimeOutput(runtimeId)) as { output?: string };
      setRuntimeOutputs((prev) => ({ ...prev, [runtimeId]: data.output || '' }));
    } catch (error) {
      notify.error(`Failed to load runtime output: ${errorText(error)}`);
    }
  }, []);

  return {
    sessions,
    memory,
    memoryDraft,
    panelData,
    runtimeOutputs,
    panelQuery,
    setPanelQuery,
    setMemoryDraft,
    setRuntimeOutputs,
    loadPanelData,
    saveMemory,
    loadRuntimeOutput,
  };
}
