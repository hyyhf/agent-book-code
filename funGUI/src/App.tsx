import { useEffect, useMemo, useRef, useState } from 'react';
import { NotificationShelf } from './components/common/NotificationShelf';
import { LeftShell } from './components/layout/LeftShell';
import { MainArea } from './components/layout/MainArea';
import { WorkspaceTree } from './components/workspace/WorkspaceTree';
import { useAgentActions } from './hooks/useAgentActions';
import { useAgentEvents } from './hooks/useAgentEvents';
import { usePanelData } from './hooks/usePanelData';
import type { AgentInfo, ChatItem, FileReadResponse, PanelKey } from './types';
import { nextId } from './utils/ids';

export default function App() {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState('');
  const [info, setInfo] = useState<AgentInfo | null>(null);
  const [panel, setPanel] = useState<PanelKey | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const [selectedPreview, setSelectedPreview] = useState<FileReadResponse | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const clientId = useMemo(() => nextId('client'), []);

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

  const eventConnection = useAgentEvents({ clientId, setInfo, setItems });

  const { sendMessage, resolveApproval, loadSession, saveSession, newSession } = useAgentActions({
    input,
    setInput,
    setItems,
    setPanel,
    setInfo,
    resetRuntimeOutputs: () => setRuntimeOutputs({}),
  });

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [items]);



  const openPanel = async (nextPanel: PanelKey) => {
    setPanel((prev) => (prev === nextPanel ? null : nextPanel));
    await loadPanelData(nextPanel);
  };

  return (
    <div className={`app-shell ${railCollapsed ? 'rail-collapsed' : ''} ${workspaceCollapsed ? 'workspace-collapsed' : ''}`}>
      <NotificationShelf />
      <LeftShell
        collapsed={railCollapsed}
        panel={panel}
        onToggleCollapsed={() => setRailCollapsed((value) => !value)}
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
        scrollRef={scrollRef}
        setInput={setInput}
        sendMessage={sendMessage}
        resolveApproval={resolveApproval}
        closePreview={() => setSelectedPreview(null)}
        saveSession={saveSession}
        workspaceCollapsed={workspaceCollapsed}
        onToggleWorkspace={() => setWorkspaceCollapsed((value) => !value)}
      />
      <WorkspaceTree
        collapsed={workspaceCollapsed}
        selectedPath={selectedPreview?.path}
        onToggle={() => setWorkspaceCollapsed((value) => !value)}
        onPreview={setSelectedPreview}
      />
    </div>
  );
}
