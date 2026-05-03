import { CheckSmall, FileSuccess } from '@icon-park/react';
import type { TaskProgress, TaskRecord } from '../../types';

export function TaskCompletionCard({
  task,
  progress,
  content,
}: {
  task: TaskRecord | null;
  progress: TaskProgress;
  content: string;
}) {
  const title = task?.title || task?.description || content || '任务已完成';
  const artifacts = Array.isArray(task?.artifacts) ? task.artifacts.filter(Boolean) : [];

  return (
    <div className="task-completion-card">
      <div className="task-completion-mark">
        <CheckSmall size={17} />
      </div>
      <div className="task-completion-body">
        <div className="task-completion-kicker">
          <FileSuccess size={14} />
          <span>任务完成</span>
          {task?.task_id ? <strong>{task.task_id}</strong> : null}
        </div>
        <div className="task-completion-title">{title}</div>
        <div className="task-completion-meta">
          <span>{progress.done}/{progress.total} done</span>
          <span>{Math.round(progress.percent)}%</span>
          {artifacts.length ? <span>{artifacts.length} 个产物</span> : null}
        </div>
        {artifacts.length ? (
          <div className="task-completion-artifacts">
            {artifacts.slice(0, 3).map((artifact) => (
              <code key={artifact}>{artifact}</code>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
