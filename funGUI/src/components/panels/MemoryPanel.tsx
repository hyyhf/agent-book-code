import { Button, Input } from '@arco-design/web-react';
import { Save } from '@icon-park/react';
import type { MemoryResponse } from '../../types';
import { MarkdownLive } from '../MarkdownLive';

const { TextArea } = Input;

export function MemoryPanel({
  memory,
  memoryDraft,
  setMemoryDraft,
  saveMemory,
}: {
  memory: MemoryResponse | null;
  memoryDraft: string;
  setMemoryDraft: (value: string) => void;
  saveMemory: () => Promise<void>;
}) {
  return (
    <div className="memory-editor">
      <div className="memory-toolbar">
        <div className="memory-path">{memory?.path || '.funharness/MEMORY.md'}</div>
        <Button type="primary" icon={<Save />} onClick={() => void saveMemory()}>
          保存记忆
        </Button>
      </div>
      <div className="memory-workspace">
        <section className="memory-pane">
          <h3>编辑</h3>
          <TextArea value={memoryDraft} onChange={setMemoryDraft} autoSize={{ minRows: 18, maxRows: 28 }} />
        </section>
        <section className="memory-pane memory-preview">
          <h3>预览</h3>
          <MarkdownLive content={memoryDraft || ' '} />
        </section>
      </div>
    </div>
  );
}
