import { memo } from 'react';
import type { ApprovalState, ChatItem } from '../../types';
import { MarkdownLive } from '../MarkdownLive';
import { CommandOutputCard } from './CommandOutputCard';
import { PlanDraftCard } from './PlanDraftCard';
import { TaskCompletionCard } from './TaskCompletionCard';
import { ThinkingBox } from './ThinkingBox';
import { ToolCard } from './ToolCard';
import { ToolGenerationCard } from './ToolGenerationCard';

export const MessageItem = memo(function MessageItem({
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
          <img src='./fh_robot3_rb.png' style={{ height: '25px', width: 'auto'}} alt="logo"/>
          <span>FunHarness</span>
          {item.streaming ? <span className="streaming-dot" /> : null}
        </div>
        <MarkdownLive content={item.content} streaming={item.streaming} />
      </div>
    );
  }
  if (item.type === 'reasoning') {
    return <ThinkingBox content={item.content} done={item.done} />;
  }
  if (item.type === 'plan_draft') {
    return <PlanDraftCard content={item.content} done={item.done} />;
  }
  if (item.type === 'task_completion') {
    return <TaskCompletionCard task={item.task} progress={item.progress} content={item.content} />;
  }
  if (item.type === 'tool_gen') {
    return <ToolGenerationCard item={item} />;
  }
  if (item.type === 'tool') {
    return <ToolCard item={item} resolveApproval={resolveApproval} />;
  }
  return <CommandOutputCard item={item} />;
});
