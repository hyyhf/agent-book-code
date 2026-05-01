import { Terminal } from '@icon-park/react';

export function ActionGrid({
  actions,
  sendCommand,
}: {
  actions: Array<[string, string]>;
  sendCommand: (value: string) => Promise<void>;
}) {
  return (
    <div className="action-grid">
      {actions.map(([label, command]) => (
        <button key={label} onClick={() => void sendCommand(command)}>
          <Terminal size={15} />
          {label}
        </button>
      ))}
    </div>
  );
}
