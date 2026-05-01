import { ActionGrid } from '../common/ActionGrid';

export function AgentsPanel({ sendCommand }: { sendCommand: (value: string) => Promise<void> }) {
  return (
    <ActionGrid
      actions={[
        ['List teammates', '/team'],
        ['View tasks', '/tasks'],
        ['Runtime lanes', '/bg'],
        ['Create plan', '/plan '],
      ]}
      sendCommand={sendCommand}
    />
  );
}
