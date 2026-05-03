import { Terminal } from '@icon-park/react';
import type { ChatItem } from '../../types';

export function CommandOutputCard({ item }: { item: Extract<ChatItem, { type: 'system' | 'status' | 'error' }> }) {
  const label = item.type === 'error' ? '错误' : item.type === 'status' ? '状态' : '命令输出';
  return (
    <div className={`command-card command-${item.type}`}>
      <div className="command-card-head">
        <span>
          <Terminal size={15} />
          {label}
        </span>
        <button onClick={() => void navigator.clipboard?.writeText(item.content)}>复制</button>
      </div>
      <pre>{item.content}</pre>
    </div>
  );
}
