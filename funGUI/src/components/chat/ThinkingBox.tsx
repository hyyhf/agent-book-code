export function ThinkingBox({ content, done }: { content: string; done: boolean }) {
  return (
    <details className="thinking-box" open={!done}>
      <summary>
        <span className="thinking-shimmer" />
        <span>{done ? '已思考' : '正在思考...'}</span>
      </summary>
      <pre>{content || 'Reasoning stream will appear here.'}</pre>
    </details>
  );
}
