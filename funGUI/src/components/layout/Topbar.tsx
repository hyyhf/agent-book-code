import { Button, Space, Tag, Tooltip } from '@arco-design/web-react';
import { MenuUnfoldOne, Save } from '@icon-park/react';
import { memo } from 'react';
import type { EventConnection } from '../../hooks/useAgentEvents';
import type { AgentInfo } from '../../types';

const eventConnectionLabels: Record<EventConnection, string> = {
  connected: '已连接',
  connecting: '连接中',
  reconnecting: '重连中',
  disconnected: '未连接',
};

export const Topbar = memo(function Topbar({
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
        <img src='./logo_fh.png' alt="logo" className="logo"/>
        <div className="title-text">
          <strong>FunHarness Studio</strong>
          <small>你的专属AI智能体</small>
        </div>
      </div>
      <Space className="topbar-actions" size={8}>
        <Tooltip content="后端事件流状态">
          <Tag className={`connection-tag connection-${eventConnection}`}>{eventConnectionLabels[eventConnection]}</Tag>
        </Tooltip>

        <Button size="small" icon={<Save />} onClick={() => void onSaveSession()}>
          保存
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
});
