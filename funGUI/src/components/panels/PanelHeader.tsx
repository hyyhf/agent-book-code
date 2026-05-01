import { Button } from '@arco-design/web-react';
import { Refresh } from '@icon-park/react';
import type { PanelKey } from '../../types';

export function panelTitle(panel: PanelKey) {
  return {
    search: '搜索',
    history: '历史会话',
    agents: 'Agents',
    dashboard: '运行面板',
    skills: '技能库',
    memory: '记忆',
    settings: '设置',
  }[panel];
}

export function panelDescription(panel: PanelKey) {
  return {
    search: '快速查找命令，或直接把输入作为任务发送。',
    history: '查看、恢复和清理本地会话记录。',
    agents: '管理团队、任务与后台运行入口。',
    dashboard: '查看任务、运行时输出和计划任务状态。',
    skills: '浏览 skills 文件夹中已安装的技能。',
    memory: '查看、编辑并保存长期记忆。',
    settings: '调整模式并查看当前运行环境。',
  }[panel];
}

export function PanelHeader({ panel, refreshPanel }: { panel: PanelKey; refreshPanel: () => Promise<void> }) {
  return (
    <div className="activity-head panel-stage-head">
      <div>
        <strong>{panelTitle(panel)}</strong>
        <span>{panelDescription(panel)}</span>
      </div>
      <Button size="small" type="secondary" icon={<Refresh />} onClick={() => void refreshPanel()}>
        刷新
      </Button>
    </div>
  );
}
