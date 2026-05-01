import { Terminal } from '@icon-park/react';
import type { ChatItem } from '../../types';

export function CommandOutputCard({ item }: { item: Extract<ChatItem, { type: 'system' | 'status' | 'error' }> }) {
  const label = item.type === 'error' ? 'Error' : item.type === 'status' ? 'Status' : 'Command Output';
  return (
    <div className={`command-card command-${item.type}`}>
      <div className="command-card-head">
        <span>
          <Terminal size={15} />
          {label}
        </span>
        <button onClick={() => void navigator.clipboard?.writeText(item.content)}>Copy</button>
      </div>
      <pre>{item.content}</pre>
    </div>
  );
}
