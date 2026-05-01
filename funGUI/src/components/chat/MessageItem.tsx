import { RobotOne } from '@icon-park/react';
import type { ApprovalState, ChatItem } from '../../types';
import { MarkdownLive } from '../MarkdownLive';
import { CommandOutputCard } from './CommandOutputCard';
import { ThinkingBox } from './ThinkingBox';
import { ToolCard } from './ToolCard';
import { ToolGenerationCard } from './ToolGenerationCard';

export function MessageItem({
  item,
  resolveApproval,
}: {
  item: ChatItem;
  resolveApproval: (approval: ApprovalState, approved: boolean, choice: string) => Promise<void>;
}) {
  if (item.type === 'user') {
    return <div className="message user-message">{item.content}</div>;
  }
  if (item.type === 'assistant') {
    return (
      <div className="message assistant-message">
        <div className="assistant-head">
          <RobotOne size={16} />
          <span>FunHarness</span>
          {item.streaming ? <span className="streaming-dot" /> : null}
        </div>
        <MarkdownLive content={item.content} />
      </div>
    );
  }
  if (item.type === 'reasoning') {
    return <ThinkingBox content={item.content} done={item.done} />;
  }
  if (item.type === 'tool_gen') {
    return <ToolGenerationCard item={item} />;
  }
  if (item.type === 'tool') {
    return <ToolCard item={item} resolveApproval={resolveApproval} />;
  }
  return <CommandOutputCard item={item} />;
}
