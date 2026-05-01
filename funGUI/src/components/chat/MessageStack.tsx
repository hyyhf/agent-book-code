import type { ApprovalState, ChatItem } from '../../types';
import { MessageItem } from './MessageItem';

export function MessageStack({
  items,
  resolveApproval,
}: {
  items: ChatItem[];
  resolveApproval: (approval: ApprovalState, approved: boolean, choice: string) => Promise<void>;
}) {
  return (
    <div className="message-stack">
      {items.map((item) => (
        <MessageItem key={item.id} item={item} resolveApproval={resolveApproval} />
      ))}
    </div>
  );
}
