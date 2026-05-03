import { Button, DatePicker, Empty, Input, Progress, Select, Tag } from '@arco-design/web-react';
import {
  Calendar,
  Check,
  Checklist,
  Clipboard,
  Command,
  Copy,
  Inbox,
  Info,
  List,
  People,
  PeoplePlus,
  Play,
  RobotOne,
  Schedule,
  Send,
  Terminal,
  Timer,
  Right,
  RightSquare,
} from '@icon-park/react';
import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';
import { api } from '../../api';
import type { AgentInfo, DashboardData, RuntimeTask, ScheduleRecord, TaskRecord, TeamInboxItem, TeamMember } from '../../types';
import { notify } from '../../utils/notify';

const { TextArea } = Input;
const Option = Select.Option;

type SectionKey = 'team' | 'tasks' | 'runtime' | 'schedule' | 'manual';

const taskStatuses: TaskRecord['status'][] = ['pending', 'in_progress', 'done', 'failed', 'skipped'];

const manualCommands = [
  {
    command: '/team',
    title: '查看或创建长期队友',
    usage: '/team | /team create <name> <role> [instructions] | /team inbox <name> | /team send <name> <message>',
    example: '/team create teacher reviewer 关注初学者是否容易理解',
  },
  {
    command: '/delegate',
    title: '把工作异步委派给队友',
    usage: '/delegate <name> <任务描述>',
    example: '/delegate teacher 检查当前实现是否适合课堂讲解',
  },
  {
    command: '/plan',
    title: '从需求生成持久任务图',
    usage: '/plan <req>',
    example: '/plan 做一个 todo.py 命令行工具，支持添加、列出、完成待办',
  },
  {
    command: '/task',
    title: '创建、读取或更新单个任务',
    usage: '/task create <title> | /task get <id> | /task update <id> status=<status> owner=<name> notes=<text>',
    example: '/task update T1 status=in_progress owner=teacher notes="开始检查边界条件"',
  },
  {
    command: '/tasks',
    title: '查看任务列表',
    usage: '/tasks',
    example: '/tasks',
  },
  {
    command: '/next',
    title: '领取下一个 ready 任务',
    usage: '/next',
    example: '/next',
  },
  {
    command: '/done',
    title: '标记任务完成',
    usage: '/done <id> [files]',
    example: '/done T1 todo.py,tests/test_todo.py',
  },
  {
    command: '/bg',
    title: '管理后台运行任务',
    usage: '/bg | /bg run <command> | /bg status <id> | /bg output <id>',
    example: '/bg run pytest -q',
  },
  {
    command: '/schedule',
    title: '创建、查看或删除定时 prompt',
    usage: '/schedule | /schedule create <name> <when> <prompt> | /schedule run <job_id> | /schedule delete <job_id>',
    example: '/schedule create 课后检查 in 10m 检查测试结果和队友反馈',
  },
];

function token(value: string) {
  return value.trim().replace(/\s+/g, '-');
}

function quotedValue(value: string) {
  const text = value.trim();
  if (!text) return '';
  if (!/[\s"]/u.test(text)) return text;
  return `"${text.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

function formatTime(value?: number) {
  if (!value) return '未记录';
  return new Date(value * 1000).toLocaleString();
}

function statusTone(status: string) {
  if (status === 'done' || status === 'idle' || status === 'enabled') return 'green';
  if (status === 'running' || status === 'in_progress' || status === 'working') return 'arcoblue';
  if (status === 'failed') return 'red';
  if (status === 'skipped' || status === 'cancelled') return 'gray';
  return 'orange';
}

function taskProgress(tasks: TaskRecord[]) {
  const done = tasks.filter((task) => task.status === 'done' || task.status === 'skipped').length;
  return {
    done,
    total: tasks.length,
    ready: tasks.filter((task) => task.status === 'pending' && task.depends_on.length === 0).length,
    percent: tasks.length ? Math.round((done / tasks.length) * 100) : 0,
  };
}

function CommandPreview({
  command,
  disabled,
  onRun,
}: {
  command: string;
  disabled?: boolean;
  onRun: (command: string) => Promise<void>;
}) {
  const canRun = Boolean(command.trim()) && !disabled;
  return (
    <div className="agent-command-preview">
      <code>{command || '填写表单后生成 slash command'}</code>
      <div>
        <Button
          size="mini"
          icon={<Copy />}
          title="复制命令，适合粘贴到聊天框或文档"
          style={{ 
            display: 'inline-flex', 
            alignItems: 'center', 
            justifyContent: 'center'
          }}
          disabled={!command.trim()}
          onClick={() => {
            void navigator.clipboard?.writeText(command);
            notify.success('命令已复制');
          }}
        />
        <Button
          size="mini"
          type="primary"
          icon={<Right size={16}/>}
          title={disabled ? 'Agent 正在运行，稍后再执行' : '发送到当前 Agent 会话执行'}
          style={{ 
            display: 'inline-flex', 
            alignItems: 'center', 
            justifyContent: 'center',
            gap: '1px' 
          }}
          disabled={!canRun}
          onClick={() => void onRun(command)}
        >
          执行
        </Button>
      </div>
    </div>
  );
}

function ManualCommandPreview({
  command,
  disabled,
  onRun,
}: {
  command: string;
  disabled?: boolean;
  onRun: (command: string) => Promise<void>;
}) {
  const canRun = Boolean(command.trim()) && !disabled;
  return (
    <div className="agent-manual-command-preview">
      <code title={command}>{command}</code>
      <div>
        <Button
          size="mini"
          icon={<Copy />}
          title="复制示例命令"
          disabled={!command.trim()}
          onClick={() => {
            void navigator.clipboard?.writeText(command);
            notify.success('命令已复制');
          }}
        />
        <Button
          size="mini"
          type="primary"
          icon={<RightSquare size={16}/>}
          style={{ 
            display: 'inline-flex', 
            alignItems: 'center', 
            justifyContent: 'center',
            gap: '1px' 
          }}
          title={disabled ? 'Agent 正在运行，稍后再执行' : '执行示例命令'}
          disabled={!canRun}
          onClick={() => void onRun(command)}
        />
      </div>
    </div>
  );
}

function FieldHelp({ text }: { text: string }) {
  return (
    <span className="agent-card-help" data-tooltip={text} aria-label={text} tabIndex={0}>
      <Info size={14} />
    </span>
  );
}

function PanelCard({
  title,
  icon,
  help,
  children,
}: {
  title: string;
  icon: ReactNode;
  help?: string;
  children: ReactNode;
}) {
  return (
    <section className="agent-card">
      <div className="agent-card-head">
        <span>
          {icon}
          <strong>{title}</strong>
        </span>
        {help ? <FieldHelp text={help} /> : null}
      </div>
      {children}
    </section>
  );
}

export function AgentsPanel({
  info,
  panelData,
  runtimeOutputs,
  sendCommand,
  refreshPanel,
  loadRuntimeOutput,
}: {
  info: AgentInfo | null;
  panelData: DashboardData;
  runtimeOutputs: Record<string, string>;
  sendCommand: (value: string) => Promise<void>;
  refreshPanel: () => Promise<void>;
  loadRuntimeOutput: (runtimeId: string) => Promise<void>;
}) {
  const [section, setSection] = useState<SectionKey>('team');
  const [memberName, setMemberName] = useState('');
  const [memberRole, setMemberRole] = useState('');
  const [memberInstructions, setMemberInstructions] = useState('');
  const [selectedMember, setSelectedMember] = useState('');
  const [teamMessage, setTeamMessage] = useState('');
  const [delegateTask, setDelegateTask] = useState('');
  const [inboxItems, setInboxItems] = useState<TeamInboxItem[]>([]);
  const [planReq, setPlanReq] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [taskId, setTaskId] = useState('');
  const [taskStatus, setTaskStatus] = useState<TaskRecord['status']>('in_progress');
  const [taskOwner, setTaskOwner] = useState('');
  const [taskNotes, setTaskNotes] = useState('');
  const [doneFiles, setDoneFiles] = useState('');
  const [runtimeCommand, setRuntimeCommand] = useState('');
  const [runtimeId, setRuntimeId] = useState('');
  const [scheduleName, setScheduleName] = useState('');
  const [scheduleWhen, setScheduleWhen] = useState('in 10m');
  const [schedulePrompt, setSchedulePrompt] = useState('');
  const [scheduleId, setScheduleId] = useState('');

  const members = panelData.team?.members || [];
  const tasks = panelData.tasks?.tasks || [];
  const runtime = panelData.runtime || [];
  const schedules = panelData.schedules || [];
  const progress = taskProgress(tasks);
  const busy = Boolean(info?.busy);

  const selectedMemberName = selectedMember || members[0]?.name || '';
  const selectedTaskId = taskId || tasks[0]?.task_id || '';
  const selectedRuntimeId = runtimeId || runtime[0]?.runtime_id || '';
  const selectedScheduleId = scheduleId || schedules[0]?.schedule_id || '';

  const commands = useMemo(
    () => ({
      createMember:
        memberName.trim() && memberRole.trim()
          ? `/team create ${token(memberName)} ${token(memberRole)}${memberInstructions.trim() ? ` ${memberInstructions.trim()}` : ''}`
          : '',
      teamInbox: selectedMemberName ? `/team inbox ${selectedMemberName}` : '',
      teamSend: selectedMemberName && teamMessage.trim() ? `/team send ${selectedMemberName} ${teamMessage.trim()}` : '',
      delegate: selectedMemberName && delegateTask.trim() ? `/delegate ${selectedMemberName} ${delegateTask.trim()}` : '',
      plan: planReq.trim() ? `/plan ${planReq.trim()}` : '',
      createTask: taskTitle.trim() ? `/task create ${taskTitle.trim()}` : '',
      getTask: selectedTaskId ? `/task get ${selectedTaskId}` : '',
      updateTask:
        selectedTaskId && (taskStatus || taskOwner.trim() || taskNotes.trim())
          ? `/task update ${selectedTaskId} status=${taskStatus}${taskOwner.trim() ? ` owner=${token(taskOwner)}` : ''}${
              taskNotes.trim() ? ` notes=${quotedValue(taskNotes)}` : ''
            }`
          : '',
      nextTask: '/next',
      doneTask: selectedTaskId ? `/done ${selectedTaskId}${doneFiles.trim() ? ` ${doneFiles.trim()}` : ''}` : '',
      bgList: '/bg',
      bgRun: runtimeCommand.trim() ? `/bg run ${runtimeCommand.trim()}` : '',
      bgStatus: selectedRuntimeId ? `/bg status ${selectedRuntimeId}` : '',
      bgOutput: selectedRuntimeId ? `/bg output ${selectedRuntimeId}` : '',
      scheduleList: '/schedule',
      scheduleCreate:
        scheduleName.trim() && scheduleWhen.trim() && schedulePrompt.trim()
          ? `/schedule create ${token(scheduleName)} ${scheduleWhen.trim()} ${schedulePrompt.trim()}`
          : '',
      scheduleRun: selectedScheduleId ? `/schedule run ${selectedScheduleId}` : '',
      scheduleDelete: selectedScheduleId ? `/schedule delete ${selectedScheduleId}` : '',
      events: '/events',
    }),
    [
      delegateTask,
      doneFiles,
      memberInstructions,
      memberName,
      memberRole,
      planReq,
      runtimeCommand,
      scheduleName,
      schedulePrompt,
      scheduleWhen,
      selectedMemberName,
      selectedRuntimeId,
      selectedScheduleId,
      selectedTaskId,
      taskNotes,
      taskOwner,
      taskStatus,
      taskTitle,
      teamMessage,
    ],
  );

  const runCommand = async (command: string) => {
    if (!command.trim() || busy) return;
    await sendCommand(command);
    window.setTimeout(() => void refreshPanel(), 900);
  };

  const loadInbox = async (name: string) => {
    if (!name) return;
    try {
      const result = await api.teamInbox(name);
      setInboxItems(result.items);
      notify.success(`${name} inbox 已加载`);
    } catch (error) {
      notify.error(`读取 inbox 失败: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  return (
    <div className="agents-workbench">
      <div className="agents-overview">
        <div className="agents-stat">
          <People size={20} />
          <span>队友</span>
          <strong>{members.length}</strong>
        </div>
        <div className="agents-stat">
          <Checklist size={20} />
          <span>Ready 任务</span>
          <strong>{progress.ready}</strong>
        </div>
        <div className="agents-stat">
          <Timer size={20} />
          <span>后台</span>
          <strong>{runtime.length}</strong>
        </div>
        <div className="agents-stat">
          <Calendar size={20} />
          <span>计划</span>
          <strong>{schedules.length}</strong>
        </div>
        <div className="agents-stat">
          <RobotOne size={20} />
          <span>状态</span>
          <strong>{busy ? '运行中' : '空闲'}</strong>
        </div>
      </div>

      <div className="agents-tabs" role="tablist">
        {[
          ['team', '团队', <People size={16} />],
          ['tasks', '任务', <Checklist size={16} />],
          ['runtime', '后台', <Terminal size={16} />],
          ['schedule', '计划', <Schedule size={16} />],
          ['manual', '命令手册', <Command size={16} />],
        ].map(([key, label, icon]) => (
          <button
            key={key as string}
            className={section === key ? 'active' : ''}
            onClick={() => setSection(key as SectionKey)}
            type="button"
          >
            {icon}
            {label}
          </button>
        ))}
      </div>

      {section === 'team' ? (
        <div className="agents-two-column">
          <PanelCard title="长期队友" icon={<PeoplePlus size={17} />} help="/team create <name> <role> [instructions]。name 和 role 建议用短 token，instructions 可以写自然语言。">
            <div className="agent-form-grid">
              <Input value={memberName} onChange={setMemberName} placeholder="name，例如 teacher" />
              <Input value={memberRole} onChange={setMemberRole} placeholder="role，例如 reviewer" />
              <TextArea value={memberInstructions} onChange={setMemberInstructions} placeholder="长期指令，例如：关注初学者是否容易理解" autoSize={{ minRows: 2, maxRows: 4 }} />
            </div>
            <CommandPreview command={commands.createMember} disabled={busy} onRun={runCommand} />
            <div className="agent-list">
              {members.length === 0 ? <Empty description="还没有队友，先创建一个 reviewer 或 teacher" /> : null}
              {members.map((member: TeamMember) => (
                <button className="agent-member-row" key={member.name} onClick={() => setSelectedMember(member.name)} type="button">
                  <span>
                    <strong>{member.name}</strong>
                    <small>{member.role}</small>
                  </span>
                  <Tag color={statusTone(member.status)}>{member.status}</Tag>
                  <Tag>{member.inbox_count} inbox</Tag>
                </button>
              ))}
            </div>
          </PanelCard>

          <PanelCard title="沟通与委派" icon={<Send size={17} />} help="/delegate 会把工作交给长期队友，并在后台 runtime lane 中运行。">
            <Select className="agent-select" value={selectedMemberName || undefined} placeholder="选择队友" onChange={(value) => setSelectedMember(String(value || ''))}>
              {members.map((member) => (
                <Option key={member.name} value={member.name}>
                  {member.name} · {member.role}
                </Option>
              ))}
            </Select>
            <TextArea value={teamMessage} onChange={setTeamMessage} placeholder="发送到 teammate inbox 的消息" autoSize={{ minRows: 2, maxRows: 4 }} />
            <CommandPreview command={commands.teamSend} disabled={busy || !selectedMemberName} onRun={runCommand} />
            <TextArea value={delegateTask} onChange={setDelegateTask} placeholder="要委派的工作，例如：检查当前任务拆分是否清楚" autoSize={{ minRows: 2, maxRows: 5 }} />
            <CommandPreview command={commands.delegate} disabled={busy || !selectedMemberName} onRun={runCommand} />
            <div className="agent-inline-actions">
              <Button icon={<Inbox />} disabled={!selectedMemberName} onClick={() => void loadInbox(selectedMemberName)}>
                在面板查看 inbox
              </Button>
              <CommandPreview command={commands.teamInbox} disabled={busy || !selectedMemberName} onRun={runCommand} />
            </div>
            <div className="agent-inbox-preview">
              {inboxItems.length === 0 ? <Empty description="inbox 内容会显示在这里" /> : null}
              {inboxItems.map((item, index) => (
                <div className="agent-inbox-item" key={`${item.timestamp}-${index}`}>
                  <span>
                    {item.from} → {item.to} · {formatTime(item.timestamp)}
                  </span>
                  <p>{item.content}</p>
                </div>
              ))}
            </div>
            <div className="agent-note">
              一次性 SubAgent 目前是工具能力，不是稳定 slash 入口；这个面板优先提供长期 teammate 的可靠 GUI。
            </div>
          </PanelCard>
        </div>
      ) : null}

      {section === 'tasks' ? (
        <div className="agents-two-column">
          <PanelCard title="任务图" icon={<Checklist size={17} />} help="/plan 会调用模型拆任务；/tasks、/next、/done 用于查看和推进任务。">
            <Progress percent={progress.percent} size="small" />
            <div className="agent-progress-line">
              {progress.done}/{progress.total} done · {progress.ready} ready
            </div>
            <TextArea value={planReq} onChange={setPlanReq} placeholder="输入需求，生成任务图" autoSize={{ minRows: 3, maxRows: 6 }} />
            <CommandPreview command={commands.plan} disabled={busy} onRun={runCommand} />
            <Input value={taskTitle} onChange={setTaskTitle} placeholder="手动创建任务标题" />
            <CommandPreview command={commands.createTask} disabled={busy} onRun={runCommand} />
            <CommandPreview command={commands.nextTask} disabled={busy} onRun={runCommand} />
          </PanelCard>

          <PanelCard title="任务列表与更新" icon={<List size={17} />} help="/task get 查看 JSON；/task update 修改状态、负责人和备注；/done 记录完成产物。">
            <Select className="agent-select" value={selectedTaskId || undefined} placeholder="选择任务" onChange={(value) => setTaskId(String(value || ''))}>
              {tasks.map((task) => (
                <Option key={task.task_id} value={task.task_id}>
                  {task.task_id} · {task.title}
                </Option>
              ))}
            </Select>
            <div className="agent-task-list">
              {tasks.length === 0 ? <Empty description="还没有任务。用 /plan 或 /task create 开始。" /> : null}
              {tasks.map((task) => (
                <div className="agent-task-row" key={task.task_id}>
                  <div>
                    <strong>{task.task_id}: {task.title}</strong>
                    <small>{task.description || task.verify || '无描述'}</small>
                    <span>
                      {task.owner ? `@${task.owner}` : '未分配'}
                      {task.depends_on.length ? ` · blocked by ${task.depends_on.join(', ')}` : ''}
                    </span>
                  </div>
                  <Tag color={statusTone(task.status)}>{task.status}</Tag>
                </div>
              ))}
            </div>
            <CommandPreview command={commands.getTask} disabled={busy || !selectedTaskId} onRun={runCommand} />
            <div className="agent-form-grid">
              <Select className="agent-select" value={taskStatus} onChange={(value) => setTaskStatus(String(value) as TaskRecord['status'])}>
                {taskStatuses.map((status) => (
                  <Option key={status} value={status}>
                    {status}
                  </Option>
                ))}
              </Select>
              <Input value={taskOwner} onChange={setTaskOwner} placeholder="owner，例如 teacher" />
              <TextArea value={taskNotes} onChange={setTaskNotes} placeholder="notes，可包含空格" autoSize={{ minRows: 2, maxRows: 4 }} />
            </div>
            <CommandPreview command={commands.updateTask} disabled={busy || !selectedTaskId} onRun={runCommand} />
            <Input value={doneFiles} onChange={setDoneFiles} placeholder="完成产物，可选：file1.py,file2.md" />
            <CommandPreview command={commands.doneTask} disabled={busy || !selectedTaskId} onRun={runCommand} />
          </PanelCard>
        </div>
      ) : null}

      {section === 'runtime' ? (
        <div className="agents-two-column">
          <PanelCard title="后台运行" icon={<Terminal size={17} />} help="/bg run 适合运行测试、构建等慢命令；完整输出会落盘并可稍后读取。">
            <TextArea value={runtimeCommand} onChange={setRuntimeCommand} placeholder="例如：pytest -q 或 npm run build" autoSize={{ minRows: 2, maxRows: 4 }} />
            <CommandPreview command={commands.bgRun} disabled={busy} onRun={runCommand} />
            <CommandPreview command={commands.bgList} disabled={busy} onRun={runCommand} />
            <Select className="agent-select" value={selectedRuntimeId || undefined} placeholder="选择 runtime task" onChange={(value) => setRuntimeId(String(value || ''))}>
              {runtime.map((task) => (
                <Option key={task.runtime_id} value={task.runtime_id}>
                  {task.runtime_id} · {task.status}
                </Option>
              ))}
            </Select>
            <CommandPreview command={commands.bgStatus} disabled={busy || !selectedRuntimeId} onRun={runCommand} />
            <CommandPreview command={commands.bgOutput} disabled={busy || !selectedRuntimeId} onRun={runCommand} />
          </PanelCard>

          <PanelCard title="运行记录" icon={<Timer size={17} />} help="这里读取结构化 runtime 状态；Output 按钮读取完整输出文件，不会重新运行命令。">
            <div className="agent-runtime-list">
              {runtime.length === 0 ? <Empty description="还没有后台任务" /> : null}
              {runtime.map((task: RuntimeTask) => (
                <div className="agent-runtime-row" key={task.runtime_id}>
                  <div>
                    <strong>{task.runtime_id}</strong>
                    <small>{task.description || task.kind}</small>
                    <span>{formatTime(task.started_at || task.created_at)}</span>
                  </div>
                  <Tag color={statusTone(task.status)}>{task.status}</Tag>
                  <Button size="mini" onClick={() => void loadRuntimeOutput(task.runtime_id)}>
                    Output
                  </Button>
                  {task.result_preview ? <pre>{task.result_preview}</pre> : null}
                  {runtimeOutputs[task.runtime_id] ? <pre>{runtimeOutputs[task.runtime_id]}</pre> : null}
                </div>
              ))}
            </div>
          </PanelCard>
        </div>
      ) : null}

      {section === 'schedule' ? (
        <div className="agents-two-column">
          <PanelCard title="创建定时 prompt" icon={<Schedule size={17} />} help="when 支持相对时间、指定日期时间，或每 5 分钟重复执行。">
            <Input value={scheduleName} onChange={setScheduleName} placeholder="name，例如 review" />
            <Input value={scheduleWhen} onChange={setScheduleWhen} placeholder="when，例如 in 10m" />
            <div className="agent-chip-row">
              {['in 10m', 'in 1h'].map((value) => (
                <button key={value} onClick={() => setScheduleWhen(value)} type="button">
                  {value}
                </button>
              ))}
              <DatePicker
                className="agent-schedule-datetime"
                value={/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/u.test(scheduleWhen) ? scheduleWhen : undefined}
                format="YYYY-MM-DDTHH:mm:ss"
                showTime={{ format: 'HH:mm:ss' }}
                placeholder="选择触发时间"
                allowClear={false}
                onChange={(value) => {
                  if (value) setScheduleWhen(value);
                }}
              />
              <button onClick={() => setScheduleWhen('*/5 * * * *')} type="button" title="cron：每 5 分钟重复执行一次">
                每 5 分钟
              </button>
            </div>
            <TextArea value={schedulePrompt} onChange={setSchedulePrompt} placeholder="到点触发的 prompt" autoSize={{ minRows: 3, maxRows: 6 }} />
            <CommandPreview command={commands.scheduleCreate} disabled={busy} onRun={runCommand} />
            <CommandPreview command={commands.scheduleList} disabled={busy} onRun={runCommand} />
          </PanelCard>

          <PanelCard title="计划列表" icon={<Calendar size={17} />} help="/schedule delete <job_id> 会删除对应调度记录。">
            <Select className="agent-select" value={selectedScheduleId || undefined} placeholder="选择计划" onChange={(value) => setScheduleId(String(value || ''))}>
              {schedules.map((schedule) => (
                <Option key={schedule.schedule_id} value={schedule.schedule_id}>
                  {schedule.schedule_id} · {schedule.name}
                </Option>
              ))}
            </Select>
            <div className="agent-note">
              定时 prompt 到点后会自动进入后台执行。这里会显示关联 runtime；点击 Output 查看完整结果。
            </div>
            <CommandPreview command={commands.events} disabled={busy} onRun={runCommand} />
            <CommandPreview command={commands.scheduleRun} disabled={busy || !selectedScheduleId} onRun={runCommand} />
            <CommandPreview command={commands.scheduleDelete} disabled={busy || !selectedScheduleId} onRun={runCommand} />
            <div className="agent-schedule-list">
              {schedules.length === 0 ? <Empty description="还没有定时 prompt" /> : null}
              {schedules.map((schedule: ScheduleRecord) => {
                const scheduleRuntime = schedule.last_runtime_id
                  ? runtime.find((task) => task.runtime_id === schedule.last_runtime_id)
                  : undefined;
                const status = scheduleRuntime?.status || (schedule.enabled ? 'on' : 'off');
                return (
                  <div className="agent-schedule-row" key={schedule.schedule_id}>
                    <div>
                      <strong>{schedule.name}</strong>
                      <small>{schedule.when} · {schedule.schedule_id}</small>
                      <small>
                        {schedule.last_fired_at
                          ? `已触发：${formatTime(schedule.last_fired_at)}`
                          : schedule.enabled && schedule.next_fire_at
                            ? `下次触发：${formatTime(schedule.next_fire_at)}`
                            : '尚未触发'}
                      </small>
                      {schedule.last_runtime_id ? <small>runtime: {schedule.last_runtime_id}</small> : null}
                      {schedule.last_run_error ? <small>error: {schedule.last_run_error}</small> : null}
                      <p>{schedule.prompt}</p>
                      {scheduleRuntime?.result_preview ? <pre>{scheduleRuntime.result_preview}</pre> : null}
                      {schedule.last_runtime_id && runtimeOutputs[schedule.last_runtime_id] ? (
                        <pre>{runtimeOutputs[schedule.last_runtime_id]}</pre>
                      ) : null}
                    </div>
                    <div className="agent-schedule-actions">
                      <Tag color={statusTone(status)}>{status}</Tag>
                      {schedule.last_runtime_id ? (
                        <Button size="mini" onClick={() => void loadRuntimeOutput(schedule.last_runtime_id)}>
                          Output
                        </Button>
                      ) : (
                        <Button size="mini" type="primary" disabled={busy} onClick={() => void runCommand(`/schedule run ${schedule.schedule_id}`)}>
                          执行
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </PanelCard>
        </div>
      ) : null}

      {section === 'manual' ? (
        <div className="agent-manual-grid">
          {manualCommands.map((item) => (
            <section className="agent-command-card" key={item.command}>
              <div className="agent-command-card-head">
                <Clipboard size={17} />
                <strong>{item.command}</strong>
                <span>{item.title}</span>
              </div>
              <code className="agent-command-usage">{item.usage}</code>
              <ManualCommandPreview command={item.example} disabled={busy} onRun={runCommand} />
            </section>
          ))}
          <section className="agent-note-card">
            <Check size={18} />
            <div>
              <strong>推荐工作流</strong>
              <p>先用 /plan 拆任务，再用 /team create 建队友，然后 /delegate 分工，最后用 /bg 和 /schedule 跟踪执行。</p>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
