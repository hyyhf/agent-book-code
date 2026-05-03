import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NotificationShelf } from './components/common/NotificationShelf';
import { LeftShell } from './components/layout/LeftShell';
import { MainArea } from './components/layout/MainArea';
import { WorkspaceTree } from './components/workspace/WorkspaceTree';
import { ElectronTitlebar } from './components/layout/ElectronTitlebar';
import { useAgentActions } from './hooks/useAgentActions';
import { useAgentEvents } from './hooks/useAgentEvents';
import { usePanelData } from './hooks/usePanelData';
import type { AgentInfo, AttachmentRecord, ChatItem, FileReadResponse, PanelKey } from './types';
import { api } from './api';
import { nextId } from './utils/ids';
import { notify } from './utils/notify';

export default function App() {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState('');
  const [info, setInfo] = useState<AgentInfo | null>(null);
  const [panel, setPanel] = useState<PanelKey | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const [selectedPreview, setSelectedPreview] = useState<FileReadResponse | null>(null);
  const [attachments, setAttachments] = useState<AttachmentRecord[]>([]);
  const [attachmentsBusy, setAttachmentsBusy] = useState(false);
  const [planMode, setPlanMode] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const previousBusy = useRef(false);
  const scrollFrame = useRef<number | null>(null);
  const clientId = useMemo(() => nextId('client'), []);
  const isElectron = Boolean(window.funharness?.isElectron);

  const {
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
  } = usePanelData(panel);

  const refreshAttachments = useCallback(async () => {
    setAttachments(await api.attachments());
  }, []);

  const resetRuntimeOutputs = useCallback(() => setRuntimeOutputs({}), [setRuntimeOutputs]);
  const toggleRailCollapsed = useCallback(() => setRailCollapsed((value) => !value), []);
  const toggleWorkspaceCollapsed = useCallback(() => setWorkspaceCollapsed((value) => !value), []);
  const closePreview = useCallback(() => setSelectedPreview(null), []);

  const eventConnection = useAgentEvents({ clientId, setInfo, setItems, setAttachments });

  const { sendMessage, resolveApproval, loadSession, saveSession, newSession } = useAgentActions({
    input,
    setInput,
    setItems,
    setPanel,
    setInfo,
    refreshAttachments,
    hasAttachments: attachments.length > 0,
    planMode,
    resetRuntimeOutputs,
  });

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return undefined;
    const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    if (distanceFromBottom > 360) return undefined;

    if (scrollFrame.current !== null) {
      window.cancelAnimationFrame(scrollFrame.current);
    }
    scrollFrame.current = window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
      scrollFrame.current = null;
    });

    return () => {
      if (scrollFrame.current !== null) {
        window.cancelAnimationFrame(scrollFrame.current);
        scrollFrame.current = null;
      }
    };
  }, [items]);

  useEffect(() => {
    const busy = Boolean(info?.busy);
    if (previousBusy.current && !busy && (panel === 'agents' || panel === 'dashboard')) {
      void loadPanelData(panel);
    }
    previousBusy.current = busy;
  }, [info?.busy, loadPanelData, panel]);

  useEffect(() => {
    if (panel !== 'agents' && panel !== 'dashboard') return undefined;
    const timer = window.setInterval(() => {
      void loadPanelData(panel);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadPanelData, panel]);

  useEffect(() => {
    void refreshAttachments().catch((error) => {
      notify.error(`Failed to load attachments: ${error instanceof Error ? error.message : String(error)}`);
    });
  }, []);

  const uploadAttachments = async (files: File[]) => {
    if (!files.length) return;
    setAttachmentsBusy(true);
    try {
      const result = await api.uploadAttachments(files);
      setAttachments(result.attachments);
    } catch (error) {
      notify.error(`Failed to upload attachment: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setAttachmentsBusy(false);
    }
  };

  const detachAttachment = async (attachmentId: string) => {
    const previous = attachments;
    setAttachments((items) => items.filter((item) => item.id !== attachmentId));
    try {
      const result = await api.detachAttachment(attachmentId);
      setAttachments(result.attachments);
    } catch (error) {
      setAttachments(previous);
      notify.error(`Failed to remove attachment: ${error instanceof Error ? error.message : String(error)}`);
    }
  };


  const openPanel = useCallback(async (nextPanel: PanelKey) => {
    setPanel((prev) => (prev === nextPanel ? null : nextPanel));
    await loadPanelData(nextPanel);
  }, [loadPanelData]);

  return (
    <>
      <ElectronTitlebar />
      <div className={`app-shell ${isElectron ? 'electron-shell' : ''} ${railCollapsed ? 'rail-collapsed' : ''} ${workspaceCollapsed ? 'workspace-collapsed' : ''}`}>
        <NotificationShelf />
        <LeftShell
          collapsed={railCollapsed}
          panel={panel}
          onToggleCollapsed={toggleRailCollapsed}
          onOpenPanel={openPanel}
          onNewSession={newSession}
          setPanel={setPanel}
        />
        <MainArea
          panel={panel}
          setPanel={setPanel}
          loadSession={loadSession}
          memory={memory}
          memoryDraft={memoryDraft}
          panelData={panelData}
          panelQuery={panelQuery}
          runtimeOutputs={runtimeOutputs}
          setPanelQuery={setPanelQuery}
          setMemoryDraft={setMemoryDraft}
          saveMemory={saveMemory}
          refreshPanel={() => loadPanelData()}
          loadRuntimeOutput={loadRuntimeOutput}
          items={items}
          input={input}
          info={info}
          eventConnection={eventConnection}
          selectedPreview={selectedPreview}
          attachments={attachments}
          attachmentsBusy={attachmentsBusy}
          planMode={planMode}
          scrollRef={scrollRef}
          setInput={setInput}
          setPlanMode={setPlanMode}
          sendMessage={sendMessage}
          uploadAttachments={uploadAttachments}
          detachAttachment={detachAttachment}
          resolveApproval={resolveApproval}
          closePreview={closePreview}
          saveSession={saveSession}
          workspaceCollapsed={workspaceCollapsed}
          onToggleWorkspace={toggleWorkspaceCollapsed}
        />
        <WorkspaceTree
          collapsed={workspaceCollapsed}
          selectedPath={selectedPreview?.path}
          onToggle={toggleWorkspaceCollapsed}
          onPreview={setSelectedPreview}
        />
      </div>
    </>
  );
}
