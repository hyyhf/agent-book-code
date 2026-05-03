import { Button, Empty, Popconfirm, Spin } from '@arco-design/web-react';
import { Delete, Play, OpenDoor, Time } from '@icon-park/react';
import { useEffect, useState } from 'react';
import { api } from '../../api';
import type { SessionSummary } from '../../types';
import { notify } from '../../utils/notify';

export function HistoryManager({ onLoadSession }: { onLoadSession: (id: string) => Promise<void> }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());

  const fetchHistory = async () => {
    setLoading(true);
    try {
      const data = await api.sessions();
      setSessions(data);
    } catch {
      notify.error('加载历史失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchHistory();
  }, []);

  const handleDelete = async (id: string) => {
    try {
      await api.deleteSession(id);
      notify.success('会话已删除');
      setDeletingIds((prev) => new Set(prev).add(id));
      setTimeout(() => {
        setSessions((prev) => prev.filter((s) => s.id !== id));
        setDeletingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }, 180);
    } catch {
      notify.error('删除会话失败');
    }
  };

  const handleLoad = async (id: string) => {
    try {
      await onLoadSession(id);
    } catch {
      // The shared action hook already renders the detailed error.
    }
  };

  if (loading) {
    return (
      <div className="manager-stage center">
        <Spin dot />
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="manager-stage center">
        <Empty description="还没有历史会话" />
      </div>
    );
  }

  return (
    <div className="manager-stage">
      <div className="manager-header">
        <h2>历史会话</h2>
        <p>查看、恢复和清理本地会话记录。</p>
      </div>
      <div className="manager-list">
        {sessions.map((session) => (
          <div className={`manager-row ${deletingIds.has(session.id) ? 'deleting' : ''}`} key={session.id}>
            <div className="manager-row-main">
              <strong>{session.title || 'Untitled Session'}</strong>
              <span>
                <Time size={14} /> {new Date(session.updated_at).toLocaleString()} · {session.message_count} messages
              </span>
            </div>
            <div className="manager-row-actions">
              <Button size="small" type="primary" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }} onClick={() => void handleLoad(session.id)}>
                <OpenDoor /><span> 加载</span>
              </Button>
              <Popconfirm title="确定要删除这个会话吗？" onOk={() => void handleDelete(session.id)}>
                <Button size="small" status="danger" icon={<Delete />} />
              </Popconfirm>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
