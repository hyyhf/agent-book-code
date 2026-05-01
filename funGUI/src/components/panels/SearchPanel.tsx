import { Empty, Input } from '@arco-design/web-react';
import { Terminal } from '@icon-park/react';
import { slashCommands } from '../../constants';

export function SearchPanel({
  query,
  setQuery,
  sendCommand,
}: {
  query: string;
  setQuery: (value: string) => void;
  sendCommand: (value: string) => Promise<void>;
}) {
  const trimmed = query.trim();
  const matches = slashCommands.filter((command) => command.includes(trimmed));

  return (
    <div className="search-panel-stage">
      <Input.Search value={query} onChange={setQuery} placeholder="搜索命令，或输入 prompt..." />
      {trimmed && !trimmed.startsWith('/') ? (
        <button className="prompt-send-button" onClick={() => void sendCommand(trimmed)}>
          <Terminal size={15} />
          发送为任务
        </button>
      ) : null}
      <div className="action-grid">
        {matches.length === 0 ? <Empty description="没有匹配的命令" /> : null}
        {matches.map((command) => (
          <button key={command} onClick={() => void sendCommand(command)}>
            <Terminal size={15} />
            {command}
          </button>
        ))}
      </div>
    </div>
  );
}
