import { Button, Space, Tag, Tooltip } from '@arco-design/web-react';
import { MenuFoldOne, MenuUnfoldOne, PauseOne, Save } from '@icon-park/react';
import { api } from '../../api';
import type { EventConnection } from '../../hooks/useAgentEvents';
import type { AgentInfo } from '../../types';


export function Topbar({
  info,
  eventConnection,
  onSaveSession,
  workspaceCollapsed,
  onToggleWorkspace,
}: {
  info: AgentInfo | null;
  eventConnection: EventConnection;
  onSaveSession: () => Promise<void>;
  workspaceCollapsed: boolean;
  onToggleWorkspace: () => void;
}) {
  return (
    <header className="topbar">
      <div className="topbar-title">
        <strong>FunHarness GUI</strong>
        <small>Local Agent Workbench</small>
      </div>
      <Space className="topbar-actions" size={8}>
        <Tooltip content="Backend event stream status">
          <Tag className={`connection-tag connection-${eventConnection}`}>{eventConnection}</Tag>
        </Tooltip>

        <Button size="small" icon={<Save />} onClick={() => void onSaveSession()}>
          Save
        </Button>
        <Button size="small" status="danger" icon={<PauseOne />} disabled={!info?.busy} onClick={() => void api.interrupt()}>
          Stop
        </Button>
        {workspaceCollapsed ? (
          <>
            <div style={{ width: 1 }} />
            <Button size="small" type="text" icon={<MenuUnfoldOne />} onClick={onToggleWorkspace} style={{ color: 'var(--ink-soft)' }} />
          </>
        ) : null}
      </Space>
    </header>
  );
}
