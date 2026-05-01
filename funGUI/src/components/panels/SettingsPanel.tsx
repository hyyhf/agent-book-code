import { Space } from '@arco-design/web-react';
import { api } from '../../api';
import type { AgentInfo } from '../../types';
import { ActionGrid } from '../common/ActionGrid';
import { ModeSwitch } from '../common/ModeSwitch';

export function SettingsPanel({
  info,
  sendCommand,
}: {
  info: AgentInfo | null;
  sendCommand: (value: string) => Promise<void>;
}) {
  return (
    <Space direction="vertical" size="medium" style={{ width: '100%' }}>
      <div className="settings-card">
        <span>当前模型</span>
        <strong>{info?.model || 'unknown'}</strong>
      </div>
      <ModeSwitch mode={info?.mode || 'suggest'} onChange={(mode) => void api.mode(mode)} />
      <ActionGrid
        actions={[
          ['保存会话', '/save'],
          ['上下文信息', '/context'],
          ['权限设置', '/perms'],
          ['导出数据', '/export'],
        ]}
        sendCommand={sendCommand}
      />
    </Space>
  );
}
