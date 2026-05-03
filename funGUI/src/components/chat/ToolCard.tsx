import { Button, Space, Tag } from '@arco-design/web-react';
import { Right, Terminal } from '@icon-park/react';
import type { ApprovalState, ChatItem } from '../../types';
import { compactJson } from '../../utils/format';

export function ToolCard({
  item,
  resolveApproval,
}: {
  item: Extract<ChatItem, { type: 'tool' }>;
  resolveApproval: (approval: ApprovalState, approved: boolean, choice: string) => Promise<void>;
}) {
  const hasResult = item.result !== undefined;
  const isReadFile = item.name === 'tool_read_file';
  const statusText = `${item.name}工具${hasResult ? '已执行' : '正在执行'}`;

  return (
    <div className="tool-card tool-event-card">
      <details>
        <summary className="tool-card-head">
          <div className="tool-card-title">
            <Terminal size={15} />
            <strong className={hasResult ? '' : 'tool-status-running'}>{statusText}</strong>
          </div>
          <div className="tool-card-actions">
            <Tag className={`risk risk-${item.risk}`}>{item.risk}</Tag>
            <Right className="tool-card-chevron" size={14} />
          </div>
        </summary>
        <div className="tool-detail-body">
          <div className="tool-section">
            <span>参数</span>
            <pre>{compactJson(item.preview) || '无参数'}</pre>
          </div>
          {hasResult ? (
            <div className="tool-section tool-result">
              <span>结果</span>
              {isReadFile ? <p className="tool-result-note">文件内容已成功读取，详细内容已省略。</p> : <pre>{item.result || '工具已执行，无返回内容。'}</pre>}
            </div>
          ) : (
            <div className="tool-section tool-pending">
              <span>状态</span>
              <p>工具仍在执行中，展开后会在这里显示结果。</p>
            </div>
          )}
        </div>
      </details>
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
