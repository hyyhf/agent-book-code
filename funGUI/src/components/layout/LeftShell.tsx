import {
  AddOne,
  ChartHistogram,
  FileText,
  Home,
  Magic,
  MenuFoldOne,
  MenuUnfoldOne,
  RobotOne,
  Tips,
  SettingTwo,
  Time,
  MemoryCardOne
} from '@icon-park/react';
import type { PanelKey } from '../../types';
import { RailButton } from './RailButton';

export function LeftShell({
  collapsed,
  panel,
  onToggleCollapsed,
  onOpenPanel,
  onNewSession,
  setPanel,
}: {
  collapsed: boolean;
  panel: PanelKey | null;
  onToggleCollapsed: () => void;
  onOpenPanel: (panel: PanelKey) => Promise<void>;
  onNewSession: () => Promise<void>;
  setPanel: (panel: PanelKey | null) => void;
}) {
  return (
    <aside className="left-shell">
      <div className="rail-top">
        <button className="icon-button brand-button" onClick={onToggleCollapsed} aria-label={collapsed ? '展开导航' : '收起导航'}>
          {collapsed ? <MenuUnfoldOne /> : <MenuFoldOne />}
        </button>
        {!collapsed ? <strong>FunHarness</strong> : null}
      </div>
      <nav className="rail">
        <RailButton collapsed={collapsed} icon={<Home size={20} />} label="主页" description="回到对话" active={!panel} onClick={() => setPanel(null)} />
        <RailButton collapsed={collapsed} icon={<AddOne size={20} />} label="新会话" description="开始新的上下文" onClick={() => void onNewSession()} />
        <RailButton collapsed={collapsed} icon={<Tips  size={20} />} label="命令" description="命令与提示" active={panel === 'search'} onClick={() => void onOpenPanel('search')} />
        <RailButton collapsed={collapsed} icon={<Time size={20} />} label="历史" description="本地会话记录" active={panel === 'history'} onClick={() => void onOpenPanel('history')} />
        <RailButton collapsed={collapsed} icon={<RobotOne size={20} />} label="Agents" description="团队与任务" active={panel === 'agents'} onClick={() => void onOpenPanel('agents')} />
        <RailButton collapsed={collapsed} icon={<ChartHistogram size={20} />} label="运行面板" description="任务、日志、计划" active={panel === 'dashboard'} onClick={() => void onOpenPanel('dashboard')} />
        <RailButton collapsed={collapsed} icon={<Magic size={20} />} label="技能库" description="已安装技能" active={panel === 'skills'} onClick={() => void onOpenPanel('skills')} />
        <RailButton collapsed={collapsed} icon={<MemoryCardOne size={20} />} label="记忆" description="查看与保存" active={panel === 'memory'} onClick={() => void onOpenPanel('memory')} />
        <div className="rail-spacer" />
        <RailButton collapsed={collapsed} icon={<SettingTwo size={20} />} label="设置" description="模式与环境" active={panel === 'settings'} onClick={() => void onOpenPanel('settings')} />
      </nav>
    </aside>
  );
}
