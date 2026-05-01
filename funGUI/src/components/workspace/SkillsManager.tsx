import { Button, Empty, Spin, Tag } from '@arco-design/web-react';
import { Code, Send } from '@icon-park/react';
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
      <div className="manager-header">
        <h2>技能库</h2>
        <p>已安装的技能会从 skills 文件夹读取，并展示 SKILL.md 中的 name 与 description。</p>
      </div>
      <div className="skill-card-grid">
        {skills.map((skill) => (
          <article className="skill-install-card" key={`${skill.path}:${skill.name}`}>
            <div className="skill-card-head">
              <span className="skill-card-icon">
                <Code />
              </span>
              <Tag size="small" color="arcoblue">
                {skillFolder(skill.path)}
              </Tag>
            </div>
            <div className="skill-card-content">
              <dl className="skill-meta-list">
                <div>
                  <dt>name</dt>
                  <dd>{skill.name}</dd>
                </div>
                <div>
                  <dt>description</dt>
                  <dd>{skill.description || '暂无描述'}</dd>
                </div>
              </dl>
            </div>
            <div className="skill-card-actions">
              <Button type="primary" icon={<Send />} onClick={() => onSelectSkill(`@${skill.name} `)}>
                使用技能
              </Button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
