export function ModeSwitch({ mode, onChange }: { mode: string; onChange: (mode: string) => void }) {
  return (
    <div className="mode-switch">
      {['auto', 'suggest', 'approve'].map((item) => (
        <button className={mode === item ? 'active' : ''} key={item} onClick={() => onChange(item)}>
          {item}
        </button>
      ))}
    </div>
  );
}
