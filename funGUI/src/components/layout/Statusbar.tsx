import { Typography } from '@arco-design/web-react';
import type { EventConnection } from '../../hooks/useAgentEvents';
import type { AgentInfo } from '../../types';

const { Text } = Typography;

export function Statusbar({ info, eventConnection }: { info: AgentInfo | null; eventConnection: EventConnection }) {
  return (
    <footer className="statusbar">
      <Text type="secondary">
        {info
          ? `Events ${eventConnection} | Mode ${info.mode} | ${info.model} | Msgs ${info.messages} | ${info.tokens.toLocaleString()} tok | Team ${info.teammates} | Bg ${info.runtime_tasks} | Sched ${info.schedules}`
          : 'Connecting...'}
      </Text>
      <Text type="secondary">{info?.cost || ''}</Text>
    </footer>
  );
}
