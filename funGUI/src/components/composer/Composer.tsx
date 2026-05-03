import { memo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Button, Dropdown, Input, Menu, Switch as ArcoSwitch } from '@arco-design/web-react';
import { ArrowUp, Down, RightBranch, UploadOne, Lightning, Lock, Microphone, Puzzle, Square, Plus, Close, Shield, CheckOne } from '@icon-park/react';
import { api } from '../../api';
import type { AgentInfo, AttachmentRecord } from '../../types';
import { fileIcon } from '../workspace/fileIcon';
import { notify } from '../../utils/notify';
import './Composer.css';

const { TextArea } = Input;

const modeLabels: Record<string, string> = {
  auto: '自动执行',
  suggest: '默认权限',
  approve: '需要确认',
};

const modeIcons: Record<string, ReactNode> = {
  auto: <Lightning />,
  suggest: <Shield />,
  approve: <Lock />,
};

function formatBytes(size: number) {
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = size;
  for (const unit of units) {
    if (value < 1024 || unit === units[units.length - 1]) {
      return unit === 'B' ? `${Math.round(value)}${unit}` : `${value.toFixed(1)}${unit}`;
    }
    value /= 1024;
  }
  return `${size}B`;
}

function attachmentType(record: AttachmentRecord) {
  return (record.extension || record.mime_type || 'file').replace(/^\./, '').toUpperCase();
}

export const Composer = memo(function Composer({
  input,
  info,
  busy,
  attachments,
  attachmentsBusy,
  planMode,
  setInput,
  setPlanMode,
  sendMessage,
  uploadAttachments,
  detachAttachment,
}: {
  input: string;
  info: AgentInfo | null;
  busy: boolean;
  attachments: AttachmentRecord[];
  attachmentsBusy: boolean;
  planMode: boolean;
  setInput: (value: string) => void;
  setPlanMode: (value: boolean) => void;
  sendMessage: (value?: string) => Promise<void>;
  uploadAttachments: (files: File[]) => Promise<void>;
  detachAttachment: (attachmentId: string) => Promise<void>;
}) {
  const currentMode = info?.mode || 'suggest';
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);

  const canSend = !busy && (Boolean(input.trim()) || attachments.length > 0);
  const primaryDisabled = !busy && !canSend;
  const primaryLabel = busy ? '停止输出' : '发送消息';

  const handlePrimaryAction = () => {
    if (busy) {
      void api.interrupt();
      return;
    }
    void sendMessage();
  };

  const handleFiles = (files: FileList | File[]) => {
    const selected = Array.from(files).filter((file) => file.size >= 0);
    if (selected.length) {
      void uploadAttachments(selected);
    }
  };

  const modeMenu = (
    <Menu onClickMenuItem={(key) => void api.mode(key)}>
      <Menu.Item key="auto" className="mode-menu-item">
        <Lightning /> 自动执行
      </Menu.Item>
      <Menu.Item key="suggest" className="mode-menu-item">
        <Shield /> 默认权限
      </Menu.Item>
      <Menu.Item key="approve" className="mode-menu-item">
        <Lock /> 需要确认
      </Menu.Item>
    </Menu>
  );

  const plusMenu = (
    <Menu
      className="composer-plus-menu"
      onClickMenuItem={(key) => {
        if (key === 'upload') {
          fileInputRef.current?.click();
        }
        if (key === 'plan') {
          setPlanMode(!planMode);
        }
      }}
    >
      <Menu.Item key="upload" className="composer-plus-menu-item">
        <UploadOne size={17} />
        <span>添加照片和文件</span>
      </Menu.Item>
      <Menu.Item key="plan" className="composer-plus-menu-item">
        <RightBranch size={17} />
        <span>计划模式</span>
        <ArcoSwitch size="small" checked={planMode} />
      </Menu.Item>
      <Menu.Item key="plugins" className="composer-plus-menu-item composer-plus-menu-tail">
        <Puzzle size={17} />
        <span>插件</span>
        <Down size={13} />
      </Menu.Item>
    </Menu>
  );

  const modelMenu = (
    <Menu
      className="composer-model-menu"
      onClickMenuItem={(key) => {
        void api.selectModelProfile(key).catch((error) => {
          notify.error(`模型切换失败: ${error instanceof Error ? error.message : String(error)}`);
        });
      }}
    >
      {(info?.model_profiles?.length ? info.model_profiles : []).map((profile) => (
        <Menu.Item key={profile.id} className="composer-model-menu-item" disabled={!profile.enabled}>
          <span className={`composer-model-dot ${profile.source === 'env' ? 'env' : 'user'}`} />
          <span className="composer-model-copy">
            <strong>{profile.name}</strong>
            <small>{profile.model || '未设置模型名称'}</small>
          </span>
          {profile.id === info?.model_profile_id ? <CheckOne size={15} /> : null}
        </Menu.Item>
      ))}
    </Menu>
  );

  return (
    <div
      className={`composer-container ${dragActive ? 'drop-active' : ''}`}
      onDragOver={(event) => {
        event.preventDefault();
        if (!busy) setDragActive(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setDragActive(false);
        }
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        if (!busy) handleFiles(event.dataTransfer.files);
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="composer-file-input"
        onChange={(event) => {
          if (event.target.files) handleFiles(event.target.files);
          event.currentTarget.value = '';
        }}
      />

      {attachments.length > 0 ? (
        <div className="attachment-chip-row">
          {attachments.map((attachment) => (
            <div className="attachment-chip" key={attachment.id}>
              <span className="attachment-chip-icon">{fileIcon(attachment.original_name)}</span>
              <span className="attachment-chip-main">
                <strong title={attachment.original_name}>{attachment.original_name}</strong>
                <small>
                  {attachmentType(attachment)} {formatBytes(attachment.size)}
                </small>
              </span>
              <button
                className="attachment-remove"
                type="button"
                aria-label={`移除 ${attachment.original_name}`}
                disabled={attachmentsBusy}
                onClick={() => void detachAttachment(attachment.id)}
              >
                <Close size={10} />
              </button>
            </div>
          ))}
        </div>
      ) : null}

      {dragActive ? <div className="composer-drop-hint">松开以上传文件</div> : null}

      <TextArea
        className="composer-textarea"
        value={input}
        onChange={setInput}
        autoSize={{ minRows: attachments.length ? 2 : 3, maxRows: 7 }}
        placeholder={attachments.length ? '给 FunHarness 发送消息，或直接发送让它阅读附件' : '给 FunHarness 发送消息'}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            if (!busy) {
              void sendMessage();
            }
          }
        }}
      />
      <div className="composer-toolbar">
        <div className="composer-toolbar-left">
          <Dropdown droplist={plusMenu} trigger="click" position="bl">
            <Button
              type="text"
              icon={<Plus size={18} />}
              className="composer-icon-btn"
              loading={attachmentsBusy}
              disabled={busy}
            />
          </Dropdown>
          {planMode ? (
            <button
              className="composer-plan-chip"
              type="button"
              aria-label="关闭计划模式"
              title="关闭计划模式"
              onClick={() => setPlanMode(false)}
            >
              <RightBranch size={14} />
              <span>计划</span>
              <Close size={10} />
            </button>
          ) : null}
          <Dropdown droplist={modeMenu} trigger="click" position="bl">
            <Button type="text" className="composer-mode-btn">
              {modeIcons[currentMode]}
              <span className="mode-label">{modeLabels[currentMode] || currentMode}</span>
              <Down size={14} />
            </Button>
          </Dropdown>
        </div>

        <div className="composer-toolbar-right">
          <Dropdown droplist={modelMenu} trigger="click" position="br" disabled={busy}>
            <Button type="text" className="composer-model-btn" disabled={busy}>
              {info?.model_profile_name || info?.model || '默认模型'} <Down size={14} />
            </Button>
          </Dropdown>

          <Button type="text" icon={<Microphone size={18} />} className="composer-icon-btn" />

          <Button
            shape="circle"
            className={`composer-send-btn ${busy ? 'composer-stop-btn' : ''}`}
            icon={busy ? <Square theme='filled' size={13} /> : <ArrowUp size={18} />}
            disabled={primaryDisabled}
            aria-label={primaryLabel}
            title={primaryLabel}
            onClick={handlePrimaryAction}
          />
        </div>
      </div>
    </div>
  );
});
