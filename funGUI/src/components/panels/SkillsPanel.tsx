import { Button } from '@arco-design/web-react';
import { Magic, Star } from '@icon-park/react';
import { skillChips } from '../../constants';

export function SkillsPanel({ sendCommand }: { sendCommand: (value: string) => Promise<void> }) {
  return (
    <div className="skill-market">
      <Button icon={<Magic />} onClick={() => void sendCommand('/skills')}>
        查看已加载 Skills
      </Button>
      {skillChips.map((chip) => (
        <button key={chip} onClick={() => void sendCommand(chip)}>
          <Star size={14} />
          {chip}
        </button>
      ))}
    </div>
  );
}
