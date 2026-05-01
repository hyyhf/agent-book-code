import { Empty } from '@arco-design/web-react';
import { Time } from '@icon-park/react';
import type { SessionSummary } from '../../types';

export function HistoryPanel({
  sessions,
  loadSession,
}: {
  sessions: SessionSummary[];
  loadSession: (sessionId: string) => Promise<void>;
}) {
  return (
    <div className="session-list">
      {sessions.length === 0 ? <Empty description="暂无历史会话" /> : null}
      {sessions.map((session) => (
        <button className="session-row" key={session.id} onClick={() => void loadSession(session.id)}>
          <Time size={16} />
          <span>
            <strong>{session.title || 'Untitled session'}</strong>
            <small>
              {session.updated_at} · {session.message_count} messages
            </small>
          </span>
        </button>
      ))}
    </div>
  );
}
