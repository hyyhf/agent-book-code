import { Button, Space, Spin, Tag } from '@arco-design/web-react';
import { Terminal } from '@icon-park/react';
import type { ApprovalState, ChatItem } from '../../types';
import { compactJson } from '../../utils/format';

export function ToolCard({
  item,
  resolveApproval,
}: {
  item: Extract<ChatItem, { type: 'tool' }>;
  resolveApproval: (approval: ApprovalState, approved: boolean, choice: string) => Promise<void>;
}) {
  return (
    <div className="tool-card">
      <div className="tool-card-head">
        <div>
          <Terminal size={16} />
          <strong>{item.name}</strong>
        </div>
        <Tag className={`risk risk-${item.risk}`}>{item.risk}</Tag>
      </div>
      <div className="tool-section">
        <span>Arguments</span>
        <pre>{compactJson(item.preview)}</pre>
      </div>
      {item.result ? (
        <div className="tool-section tool-result">
          <span>Result</span>
          <pre>{item.result}</pre>
        </div>
      ) : (
        <div className="tool-pending">
          <Spin size={14} />
          Running tool...
        </div>
      )}
      {item.hookFeedback ? <div className="hook-feedback">{item.hookFeedback}</div> : null}
      {item.approval ? (
        <div className="approval-inline">
          <div>
            <strong>Approval required</strong>
            <small>{item.approval.reason}</small>
          </div>
          <Space>
            <Button size="mini" type="primary" onClick={() => void resolveApproval(item.approval!, true, 'once')}>
              Allow Once
            </Button>
            <Button size="mini" onClick={() => void resolveApproval(item.approval!, true, 'always')}>
              Always
            </Button>
            <Button size="mini" status="danger" onClick={() => void resolveApproval(item.approval!, false, '')}>
              Deny
            </Button>
          </Space>
        </div>
      ) : null}
    </div>
  );
}
