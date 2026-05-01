import { Tooltip } from '@arco-design/web-react';
import type { ReactNode } from 'react';

export function RailButton({
  icon,
  label,
  description,
  active,
  collapsed,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  description?: string;
  active?: boolean;
  collapsed: boolean;
  onClick: () => void;
}) {
  return (
    <Tooltip content={description ? `${label} · ${description}` : label} position="right" disabled={!collapsed}>
      <button className={`rail-button ${active ? 'active' : ''}`} onClick={onClick} aria-label={label}>
        <span className="rail-button-icon">{icon}</span>
        {!collapsed ? (
          <span className="rail-button-copy">
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
          </span>
        ) : null}
      </button>
    </Tooltip>
  );
}
