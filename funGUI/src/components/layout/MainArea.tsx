import type { RefObject } from 'react';
import { lazy, Suspense } from 'react';
import { Star } from '@icon-park/react';
import { slashCommands, skillChips } from '../../constants';
import type { AgentInfo, ApprovalState, ChatItem, DashboardData, FileReadResponse, MemoryResponse, PanelKey } from '../../types';
import type { EventConnection } from '../../hooks/useAgentEvents';
import { AgentsPanel } from '../panels/AgentsPanel';
import { DashboardPanel } from '../panels/DashboardPanel';
import { MemoryPanel } from '../panels/MemoryPanel';
import { PanelHeader } from '../panels/PanelHeader';
import { SearchPanel } from '../panels/SearchPanel';
import { SettingsPanel } from '../panels/SettingsPanel';
import { MessageStack } from '../chat/MessageStack';
import { Composer } from '../composer/Composer';
import { Statusbar } from './Statusbar';
import { Topbar } from './Topbar';

const FilePreviewStage = lazy(() =>
  import('../workspace/FilePreviewStage').then((module) => ({ default: module.FilePreviewStage })),
);
const HistoryManager = lazy(() =>
  import('../workspace/HistoryManager').then((module) => ({ default: module.HistoryManager })),
);
const SkillsManager = lazy(() =>
  import('../workspace/SkillsManager').then((module) => ({ default: module.SkillsManager })),
);

export function MainArea({
  panel,
  setPanel,
  loadSession,
  memory,
  memoryDraft,
  panelData,
  panelQuery,
  runtimeOutputs,
  setPanelQuery,
  setMemoryDraft,
  saveMemory,
  refreshPanel,
  loadRuntimeOutput,
  items,
  input,
  info,
  eventConnection,
  selectedPreview,
  scrollRef,
  setInput,
  sendMessage,
  resolveApproval,
  closePreview,
  saveSession,
  workspaceCollapsed,
  onToggleWorkspace,
}: {
  items: ChatItem[];
  input: string;
  info: AgentInfo | null;
  memory: MemoryResponse | null;
  memoryDraft: string;
  panelData: DashboardData;
  panelQuery: string;
  runtimeOutputs: Record<string, string>;
  eventConnection: EventConnection;
  selectedPreview: FileReadResponse | null;
  scrollRef: RefObject<HTMLDivElement | null>;
  setInput: (value: string) => void;
  setPanelQuery: (value: string) => void;
  setMemoryDraft: (value: string) => void;
  sendMessage: (value?: string) => Promise<void>;
  resolveApproval: (approval: ApprovalState, approved: boolean, choice: string) => Promise<void>;
  closePreview: () => void;
  saveSession: () => Promise<void>;
  saveMemory: () => Promise<void>;
  refreshPanel: () => Promise<void>;
  loadRuntimeOutput: (runtimeId: string) => Promise<void>;
  panel: PanelKey | null;
  setPanel: (panel: PanelKey | null) => void;
  loadSession: (id: string) => Promise<void>;
  workspaceCollapsed: boolean;
  onToggleWorkspace: () => void;
}) {
  const stageClass = selectedPreview ? 'stage has-preview' : 'stage';

  if (panel === 'history') {
    return (
      <main className="main-area">
        <Topbar info={info} eventConnection={eventConnection} onSaveSession={saveSession} workspaceCollapsed={workspaceCollapsed} onToggleWorkspace={onToggleWorkspace} />
        <Suspense fallback={<div className="manager-stage center">Loading history...</div>}>
          <HistoryManager onLoadSession={loadSession} />
        </Suspense>
        <Statusbar info={info} eventConnection={eventConnection} />
      </main>
    );
  }

  if (panel === 'skills') {
    return (
      <main className="main-area">
        <Topbar info={info} eventConnection={eventConnection} onSaveSession={saveSession} workspaceCollapsed={workspaceCollapsed} onToggleWorkspace={onToggleWorkspace} />
        <Suspense fallback={<div className="manager-stage center">Loading skills...</div>}>
          <SkillsManager
            onSelectSkill={(skill) => {
              setInput(skill);
              setPanel(null);
            }}
          />
        </Suspense>
        <Statusbar info={info} eventConnection={eventConnection} />
      </main>
    );
  }

  if (panel) {
    return (
      <main className="main-area">
        <Topbar info={info} eventConnection={eventConnection} onSaveSession={saveSession} workspaceCollapsed={workspaceCollapsed} onToggleWorkspace={onToggleWorkspace} />
        <section className="manager-stage panel-stage">
          <PanelHeader panel={panel} refreshPanel={refreshPanel} />
          <div className="panel-stage-body">
            {panel === 'search' ? <SearchPanel query={panelQuery} setQuery={setPanelQuery} sendCommand={sendMessage} /> : null}
            {panel === 'memory' ? (
              <MemoryPanel memory={memory} memoryDraft={memoryDraft} setMemoryDraft={setMemoryDraft} saveMemory={saveMemory} />
            ) : null}
            {panel === 'agents' ? <AgentsPanel sendCommand={sendMessage} /> : null}
            {panel === 'dashboard' ? (
              <DashboardPanel
                panelData={panelData}
                runtimeOutputs={runtimeOutputs}
                sendCommand={sendMessage}
                loadRuntimeOutput={loadRuntimeOutput}
              />
            ) : null}
            {panel === 'settings' ? <SettingsPanel info={info} sendCommand={sendMessage} /> : null}
          </div>
        </section>
        <Statusbar info={info} eventConnection={eventConnection} />
      </main>
    );
  }

  return (
    <main className="main-area">
      <Topbar info={info} eventConnection={eventConnection} onSaveSession={saveSession} workspaceCollapsed={workspaceCollapsed} onToggleWorkspace={onToggleWorkspace} />
      <section className={stageClass}>
        <div className={`conversation ${items.length === 0 ? 'empty-conversation' : ''}`} ref={scrollRef}>
          {items.length === 0 ? (
            <div className="hero-composer">
              <h1>嗨，有什么可以帮你的？</h1>
              <Composer input={input} info={info} setInput={setInput} sendMessage={sendMessage} busy={Boolean(info?.busy)} />
              <div className="skill-row">
                {skillChips.map((chip) => (
                  <button key={chip} onClick={() => setInput(chip)}>
                    <Star size={13} />
                    {chip}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="conversation-inner">
              <MessageStack items={items} resolveApproval={resolveApproval} />
              <footer className="composer-footer sticky-composer">
                <Composer input={input} info={info} setInput={setInput} sendMessage={sendMessage} busy={Boolean(info?.busy)} />
                <div className="slash-row">
                  {slashCommands.slice(0, 8).map((command) => (
                    <button key={command} onClick={() => void sendMessage(command)}>
                      {command}
                    </button>
                  ))}
                </div>
              </footer>
            </div>
          )}
        </div>
        {selectedPreview ? (
          <Suspense fallback={<section className="preview-stage preview-stage-loading">Loading preview...</section>}>
            <FilePreviewStage preview={selectedPreview} onClose={closePreview} />
          </Suspense>
        ) : null}
      </section>
      <Statusbar info={info} eventConnection={eventConnection} />
    </main>
  );
}
