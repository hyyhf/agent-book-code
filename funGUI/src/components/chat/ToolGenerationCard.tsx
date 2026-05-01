import { Code } from '@icon-park/react';
import type { ChatItem } from '../../types';

export function ToolGenerationCard({ item }: { item: Extract<ChatItem, { type: 'tool_gen' }> }) {
  return (
    <div className="command-card tool-gen-card">
      <div className="command-card-head">
        <span>
          <Code size={15} />
          Generating tool call {item.name ? `· ${item.name}` : ''}
        </span>
      </div>
      <pre>{item.content || 'Waiting for arguments...'}</pre>
    </div>
  );
}
