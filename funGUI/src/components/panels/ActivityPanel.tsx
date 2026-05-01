import type { AgentInfo, DashboardData, MemoryResponse, PanelKey, SessionSummary } from '../../types';
import { AgentsPanel } from './AgentsPanel';
import { DashboardPanel } from './DashboardPanel';
import { HistoryPanel } from './HistoryPanel';
import { MemoryPanel } from './MemoryPanel';
import { PanelHeader } from './PanelHeader';
import { SearchPanel } from './SearchPanel';
import { SettingsPanel } from './SettingsPanel';
import { SkillsPanel } from './SkillsPanel';

export function ActivityPanel({
  panel,
  sessions,
  memory,
  memoryDraft,
  panelData,
  query,
  info,
  runtimeOutputs,
  setQuery,
  setMemoryDraft,
  sendCommand,
  loadSession,
  saveMemory,
  refreshPanel,
  loadRuntimeOutput,
}: {
  panel: PanelKey | null;
  sessions: SessionSummary[];
  memory: MemoryResponse | null;
  memoryDraft: string;
  panelData: DashboardData;
  query: string;
  info: AgentInfo | null;
  runtimeOutputs: Record<string, string>;
  setQuery: (value: string) => void;
  setMemoryDraft: (value: string) => void;
  sendCommand: (value: string) => Promise<void>;
  loadSession: (sessionId: string) => Promise<void>;
  saveMemory: () => Promise<void>;
  refreshPanel: () => Promise<void>;
  loadRuntimeOutput: (runtimeId: string) => Promise<void>;
}) {
  if (!panel) return null;
  return (
    <section className="activity-panel">
      <PanelHeader panel={panel} refreshPanel={refreshPanel} />
      {panel === 'search' ? <SearchPanel query={query} setQuery={setQuery} sendCommand={sendCommand} /> : null}
      {panel === 'history' ? <HistoryPanel sessions={sessions} loadSession={loadSession} /> : null}
      {panel === 'memory' ? (
        <MemoryPanel memory={memory} memoryDraft={memoryDraft} setMemoryDraft={setMemoryDraft} saveMemory={saveMemory} />
      ) : null}
      {panel === 'agents' ? <AgentsPanel sendCommand={sendCommand} /> : null}
      {panel === 'dashboard' ? (
        <DashboardPanel
          panelData={panelData}
          runtimeOutputs={runtimeOutputs}
          sendCommand={sendCommand}
          loadRuntimeOutput={loadRuntimeOutput}
        />
      ) : null}
      {panel === 'skills' ? <SkillsPanel sendCommand={sendCommand} /> : null}
      {panel === 'settings' ? <SettingsPanel info={info} sendCommand={sendCommand} /> : null}
    </section>
  );
}
