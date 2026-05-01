import { Button, Input, Space, Dropdown, Menu, Typography } from '@arco-design/web-react';
import { ArrowUp, Plus, Microphone, Lightning, Shield, Lock, Down } from '@icon-park/react';
import { api } from '../../api';
import type { AgentInfo } from '../../types';
import './Composer.css'; // Let's make sure we have CSS, actually let's assume it's in index.css or main.css.

const { TextArea } = Input;
const { Text } = Typography;

const modeLabels: Record<string, string> = {
  auto: '自动执行',
  suggest: '默认权限',
  approve: '需要确认',
};

const modeIcons: Record<string, JSX.Element> = {
  auto: <Lightning />,
  suggest: <Shield />,
  approve: <Lock />,
};

export function Composer({
  input,
  info,
  busy,
  setInput,
  sendMessage,
}: {
  input: string;
  info: AgentInfo | null;
  busy: boolean;
  setInput: (value: string) => void;
  sendMessage: (value?: string) => Promise<void>;
}) {
  const currentMode = info?.mode || 'suggest';

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

  return (
    <div className="composer-container">
      <TextArea
        className="composer-textarea"
        value={input}
        onChange={setInput}
        autoSize={{ minRows: 3, maxRows: 7 }}
        placeholder="跟funharness对话，或者输入/help查看可用命令"
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            void sendMessage();
          }
        }}
        bordered={false}
      />
      <div className="composer-toolbar">
        <div className="composer-toolbar-left">
          <Button type="text" icon={<Plus size={18} />} className="composer-icon-btn" />
          <Dropdown droplist={modeMenu} trigger="click" position="bl">
            <Button type="text" className="composer-mode-btn">
              {modeIcons[currentMode]}
              <span className="mode-label">{modeLabels[currentMode] || currentMode}</span>
              <Down size={14} />
            </Button>
          </Dropdown>
        </div>
        
        <div className="composer-toolbar-right">
          <Dropdown droplist={
            <Menu>
              <Menu.Item key="model">{info?.model || '默认模型'}</Menu.Item>
            </Menu>
          } trigger="click" position="br">
            <Button type="text" className="composer-model-btn">
              {info?.model || '5.4 中'} <Down size={14} />
            </Button>
          </Dropdown>
          
          <Button type="text" icon={<Microphone size={18} />} className="composer-icon-btn" />
          
          <Button
            shape="circle"
            className="composer-send-btn"
            icon={<ArrowUp size={18} />}
            loading={busy}
            disabled={busy || !input.trim()}
            onClick={() => void sendMessage()}
          />
        </div>
      </div>
    </div>
  );
}
