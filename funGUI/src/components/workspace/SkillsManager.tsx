import { Button, Empty, Spin, Tag } from '@arco-design/web-react';
import { Code, BookmarkOne } from '@icon-park/react';
import { useEffect, useState } from 'react';
import { api } from '../../api';
import type { SkillMeta } from '../../api';
import { notify } from '../../utils/notify';

function skillFolder(path: string) {
  const normalized = path.replaceAll('\\', '/');
  return normalized.split('/').slice(-2, -1)[0] || normalized.split('/').pop() || 'skill';
}

export function SkillsManager({ onSelectSkill }: { onSelectSkill: (skill: string) => void }) {
  const [skills, setSkills] = useState<SkillMeta[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchSkills = async () => {
    setLoading(true);
    try {
      const data = await api.skills();
      setSkills(data);
    } catch {
      notify.error('加载技能失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchSkills();
  }, []);

  if (loading) {
    return (
      <div className="manager-stage center">
        <Spin dot />
      </div>
    );
  }

  if (skills.length === 0) {
    return (
      <div className="manager-stage center">
        <Empty description="还没有安装技能" />
      </div>
    );
  }

  return (
    <div className="manager-stage skills-stage">
      <div className="manager-header skills-manager-header">
        <div>
          <h1>技能库</h1>
          <p>从 <code>.funharness/skills</code> 自动读取已安装技能，点击后会把技能名写入输入框。</p>
        </div>
        <div className="skills-count-card" aria-label={`共 ${skills.length} 个技能`}>
          <strong>{skills.length}</strong>
          <span>个可用技能</span>
        </div>
      </div>

      <div className="skill-card-grid">
        {skills.map((skill) => (
          <article className="skill-install-card" key={`${skill.path}:${skill.name}`}>
            <div className="skill-card-head">
              <span className="skill-card-icon">
                <Code />
              </span>
              <Tag size="small" className="skill-source-tag">
                {skillFolder(skill.path)}
              </Tag>
            </div>
            <div className="skill-card-content">
              <span className="skill-field-label">技能名</span>
              <h3>{skill.name}</h3>
              <span className="skill-field-label">技能说明</span>
              <p>{skill.description || '暂无说明'}</p>
            </div>
            <div className="skill-card-actions">
              <Button type="primary" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap:'3px'}}onClick={() => onSelectSkill(`@${skill.name} `)}>
                <BookmarkOne /> <span>调用技能</span>
              </Button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
