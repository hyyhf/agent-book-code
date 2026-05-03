import { Plan } from '@icon-park/react';

export function PlanDraftCard({ content, done }: { content: string; done: boolean }) {
  return (
    <div className={`plan-draft-card ${done ? 'is-done' : ''}`}>
      <div className="plan-draft-head">
        <Plan size={15} />
        <span>{done ? '计划草稿已生成' : '正在生成计划草稿'}</span>
        {!done ? <span className="streaming-dot" /> : null}
      </div>
      <pre>{content || '计划草稿会显示在这里。'}</pre>
    </div>
  );
}
