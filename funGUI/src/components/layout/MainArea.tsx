import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, RefObject } from 'react';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Star } from '@icon-park/react';
import { slashCommands, skillChips } from '../../constants';
import type { AgentInfo, ApprovalState, AttachmentRecord, ChatItem, DashboardData, FileReadResponse, MemoryResponse, PanelKey } from '../../types';
import type { EventConnection } from '../../hooks/useAgentEvents';
import { AgentsPanel } from '../panels/AgentsPanel';
import { DashboardPanel } from '../panels/DashboardPanel';
import { MemoryPanel } from '../panels/MemoryPanel';
import { PanelHeader } from '../panels/PanelHeader';
import { SearchPanel } from '../panels/SearchPanel';
import { SettingsPanel } from '../panels/SettingsPanel';
import { MessageStack } from '../chat/MessageStack';
import { Composer } from '../composer/Composer';
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

const clampPreviewWidthPercent = (value: number) => Math.min(82, Math.max(50, value));

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
  attachments,
  attachmentsBusy,
  planMode,
  scrollRef,
  setInput,
  setPlanMode,
  sendMessage,
  uploadAttachments,
  detachAttachment,
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
  attachments: AttachmentRecord[];
  attachmentsBusy: boolean;
  planMode: boolean;
  scrollRef: RefObject<HTMLDivElement | null>;
  setInput: (value: string) => void;
  setPlanMode: (value: boolean) => void;
  setPanelQuery: (value: string) => void;
  setMemoryDraft: (value: string) => void;
  sendMessage: (value?: string) => Promise<void>;
  uploadAttachments: (files: File[]) => Promise<void>;
  detachAttachment: (attachmentId: string) => Promise<void>;
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
  const stageRef = useRef<HTMLElement | null>(null);
  const previewWidthPercentRef = useRef(54);
  const resizeFrameRef = useRef<number | null>(null);
  const pendingPreviewWidthRef = useRef<number | null>(null);
  const [previewWidthPercent, setPreviewWidthPercent] = useState(54);
  const [isResizingPreview, setIsResizingPreview] = useState(false);

  const applyPreviewWidth = useCallback((value: number) => {
    const width = clampPreviewWidthPercent(value);
    previewWidthPercentRef.current = width;
    stageRef.current?.style.setProperty('--preview-width', `${width}%`);
    return width;
  }, []);

  const schedulePreviewWidth = useCallback((value: number) => {
    pendingPreviewWidthRef.current = clampPreviewWidthPercent(value);
    if (resizeFrameRef.current !== null) return;

    resizeFrameRef.current = window.requestAnimationFrame(() => {
      resizeFrameRef.current = null;
      if (pendingPreviewWidthRef.current === null) return;
      applyPreviewWidth(pendingPreviewWidthRef.current);
      pendingPreviewWidthRef.current = null;
    });
  }, [applyPreviewWidth]);

  const updatePreviewWidth = useCallback((clientX: number) => {
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    if (rect.width <= 0) return;
    const nextWidth = ((rect.right - clientX) / rect.width) * 100;
    schedulePreviewWidth(nextWidth);
  }, [schedulePreviewWidth]);

  useEffect(() => {
    if (!isResizingPreview) return;

    const handlePointerMove = (event: PointerEvent) => updatePreviewWidth(event.clientX);
    const handlePointerUp = () => {
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
        resizeFrameRef.current = null;
      }
      if (pendingPreviewWidthRef.current !== null) {
        applyPreviewWidth(pendingPreviewWidthRef.current);
        pendingPreviewWidthRef.current = null;
      }
      setPreviewWidthPercent(previewWidthPercentRef.current);
      setIsResizingPreview(false);
    };
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);

    return () => {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
        resizeFrameRef.current = null;
      }
      pendingPreviewWidthRef.current = null;
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [applyPreviewWidth, isResizingPreview, updatePreviewWidth]);

  const stageStyle = selectedPreview
    ? ({ '--preview-width': `${previewWidthPercent}%` } as CSSProperties)
    : undefined;

  const handlePreviewResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    updatePreviewWidth(event.clientX);
    setIsResizingPreview(true);
  };

  const handlePreviewResizeKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    setPreviewWidthPercent((value) => applyPreviewWidth(value + (event.key === 'ArrowLeft' ? 3 : -3)));
  };

  if (panel === 'history') {
    return (
      <main className="main-area">
        <Topbar info={info} eventConnection={eventConnection} onSaveSession={saveSession} workspaceCollapsed={workspaceCollapsed} onToggleWorkspace={onToggleWorkspace} />
        <Suspense fallback={<div className="manager-stage center">正在加载历史会话...</div>}>
          <HistoryManager onLoadSession={loadSession} />
        </Suspense>
      </main>
    );
  }

  if (panel === 'skills') {
    return (
      <main className="main-area">
        <Topbar info={info} eventConnection={eventConnection} onSaveSession={saveSession} workspaceCollapsed={workspaceCollapsed} onToggleWorkspace={onToggleWorkspace} />
        <Suspense fallback={<div className="manager-stage center">正在加载技能库...</div>}>
          <SkillsManager
            onSelectSkill={(skill) => {
              setInput(skill);
              setPanel(null);
            }}
          />
        </Suspense>
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
            {panel === 'agents' ? (
              <AgentsPanel
                info={info}
                panelData={panelData}
                runtimeOutputs={runtimeOutputs}
                sendCommand={sendMessage}
                refreshPanel={refreshPanel}
                loadRuntimeOutput={loadRuntimeOutput}
              />
            ) : null}
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
      </main>
    );
  }

  return (
    <main className="main-area">
      <Topbar info={info} eventConnection={eventConnection} onSaveSession={saveSession} workspaceCollapsed={workspaceCollapsed} onToggleWorkspace={onToggleWorkspace} />
      <section className={`${stageClass} ${isResizingPreview ? 'is-resizing-preview' : ''}`} ref={stageRef} style={stageStyle}>
        <div className={`conversation ${items.length === 0 ? 'empty-conversation' : ''}`} ref={scrollRef}>
          {items.length === 0 ? (
            <div className="hero-composer">
              <img src='./logo_fh.png' alt="logo" className="logo" style={{height: '100px', width: 'auto', marginBottom: '16px' }} />
              <h1   
                style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px'
              }}>
                嗨，有什么
                <img src='./fh_text_logo_rb.png' alt="logo" className="logo" style={{height: '50px', width: 'auto'}} />
                可以帮你的？
              </h1>
              <Composer
                input={input}
                info={info}
                attachments={attachments}
                attachmentsBusy={attachmentsBusy}
                planMode={planMode}
                setInput={setInput}
                setPlanMode={setPlanMode}
                sendMessage={sendMessage}
                uploadAttachments={uploadAttachments}
                detachAttachment={detachAttachment}
                busy={Boolean(info?.busy)}
              />
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
                <Composer
                  input={input}
                  info={info}
                  attachments={attachments}
                  attachmentsBusy={attachmentsBusy}
                  planMode={planMode}
                  setInput={setInput}
                  setPlanMode={setPlanMode}
                  sendMessage={sendMessage}
                  uploadAttachments={uploadAttachments}
                  detachAttachment={detachAttachment}
                  busy={Boolean(info?.busy)}
                />
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
          <>
            <div
              className={`preview-resizer ${isResizingPreview ? 'dragging' : ''}`}
              role="separator"
              aria-label="调整预览区宽度"
              aria-orientation="vertical"
              tabIndex={0}
              onPointerDown={handlePreviewResizeStart}
              onKeyDown={handlePreviewResizeKeyDown}
            />
          <Suspense fallback={<section className="preview-stage preview-stage-loading">正在加载预览...</section>}>
            <FilePreviewStage preview={selectedPreview} onClose={closePreview} />
          </Suspense>
          </>
        ) : null}
      </section>
    </main>
  );
}
