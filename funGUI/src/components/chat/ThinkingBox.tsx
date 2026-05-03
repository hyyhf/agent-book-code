export function ThinkingBox({ content, done }: { content: string; done: boolean }) {
  return (
    <details className="thinking-box" open={!done}>
      <summary>
        <span className="thinking-shimmer" />
        <span>{done ? '已思考' : '正在思考...'}</span>
      </summary>
      <pre>{content || '思考内容会显示在这里。'}</pre>
    </details>
  );
}
