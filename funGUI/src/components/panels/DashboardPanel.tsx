import { Button, Empty, Tag } from '@arco-design/web-react';
import type { DashboardData, RuntimeTask } from '../../types';
import { ActionGrid } from '../common/ActionGrid';
import { Snapshot } from '../common/Snapshot';

function runtimeTasks(data: DashboardData): RuntimeTask[] {
  return Array.isArray(data.runtime) ? data.runtime : [];
}

export function DashboardPanel({
  panelData,
  runtimeOutputs,
  sendCommand,
  loadRuntimeOutput,
}: {
  panelData: DashboardData;
  runtimeOutputs: Record<string, string>;
  sendCommand: (value: string) => Promise<void>;
  loadRuntimeOutput: (runtimeId: string) => Promise<void>;
}) {
  const runtime = runtimeTasks(panelData);

  return (
    <>
      <ActionGrid
        actions={[
          ['Cost dashboard', '/dashboard'],
          ['Trace timeline', '/trace'],
          ['Recent logs', '/logs'],
          ['Failure analysis', '/failures'],
        ]}
        sendCommand={sendCommand}
      />
      <Snapshot title="Tasks" data={panelData.tasks} />
      <div className="snapshot runtime-snapshot">
        <strong>Runtime</strong>
        {runtime.length === 0 ? <Empty description="暂无运行时任务" /> : null}
        <div className="runtime-list">
          {runtime.map((task) => (
            <div className="runtime-row" key={task.runtime_id}>
              <div>
                <span>
                  <strong>{task.runtime_id}</strong>
                  <Tag size="small">{task.status}</Tag>
                </span>
                <small>{task.description || task.kind}</small>
              </div>
              <Button size="mini" onClick={() => void loadRuntimeOutput(task.runtime_id)}>
                Output
              </Button>
              {runtimeOutputs[task.runtime_id] ? <pre>{runtimeOutputs[task.runtime_id]}</pre> : null}
            </div>
          ))}
        </div>
      </div>
      <Snapshot title="Schedules" data={panelData.schedules} />
    </>
  );
}
