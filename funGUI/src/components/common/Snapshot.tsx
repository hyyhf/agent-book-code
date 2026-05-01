export function Snapshot({ title, data }: { title: string; data: unknown }) {
  return (
    <div className="snapshot">
      <strong>{title}</strong>
      <pre>{data ? JSON.stringify(data, null, 2) : 'Loading...'}</pre>
    </div>
  );
}
