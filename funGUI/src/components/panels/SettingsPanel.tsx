import { useEffect, useMemo, useState } from 'react';
import { Button, Input, Space, Switch as ArcoSwitch } from '@arco-design/web-react';
import { Api, CheckOne, Close, Connection, Delete, ExperimentOne, Plus, Save, Shield, Star } from '@icon-park/react';
import { api } from '../../api';
import type { AgentInfo, ModelProfile, ModelProfileDraft, ModelProfileTestResponse } from '../../types';
import { ActionGrid } from '../common/ActionGrid';
import { ModeSwitch } from '../common/ModeSwitch';
import { notify } from '../../utils/notify';

const ENV_PROFILE_ID = '__env__';

type EditableProfile = ModelProfileDraft & {
  source?: 'env' | 'user';
  has_api_key?: boolean;
  api_key_masked?: string;
};

function editableFromProfile(profile: ModelProfile): EditableProfile {
  return {
    id: profile.id,
    name: profile.name,
    base_url: profile.base_url,
    api_key: '',
    model: profile.model,
    enabled: profile.enabled,
    source: profile.source,
    has_api_key: profile.has_api_key,
    api_key_masked: profile.api_key_masked,
  };
}

function draftId() {
  return `model_${Date.now().toString(36)}`;
}

export function SettingsPanel({
  info,
  sendCommand,
}: {
  info: AgentInfo | null;
  sendCommand: (value: string) => Promise<void>;
}) {
  const [profiles, setProfiles] = useState<EditableProfile[]>([]);
  const [selectedId, setSelectedId] = useState(ENV_PROFILE_ID);
  const [defaultId, setDefaultId] = useState(ENV_PROFILE_ID);
  const [configPath, setConfigPath] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ModelProfileTestResponse | null>(null);

  const selected = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) || profiles[0],
    [profiles, selectedId],
  );
  const userProfiles = profiles.filter((profile) => profile.source !== 'env');

  const loadProfiles = async () => {
    const result = await api.modelProfiles();
    const nextProfiles = result.profiles.map(editableFromProfile);
    setProfiles(nextProfiles);
    setSelectedId(result.active_profile_id || result.default_profile_id);
    setDefaultId(result.default_profile_id);
    setConfigPath(result.config_path);
    setTestResult(null);
  };

  useEffect(() => {
    void loadProfiles().catch((error) => {
      notify.error(`加载模型配置失败: ${error instanceof Error ? error.message : String(error)}`);
    });
  }, []);

  const updateSelected = (patch: Partial<EditableProfile>) => {
    setProfiles((items) => items.map((item) => (item.id === selectedId ? { ...item, ...patch } : item)));
    setTestResult(null);
  };

  const addProfile = () => {
    const profile: EditableProfile = {
      id: draftId(),
      name: '新模型',
      base_url: '',
      api_key: '',
      model: '',
      enabled: true,
      source: 'user',
      has_api_key: false,
      api_key_masked: '',
    };
    setProfiles((items) => [...items, profile]);
    setSelectedId(profile.id);
    setTestResult(null);
  };

  const deleteSelected = () => {
    if (!selected || selected.source === 'env') return;
    const remaining = profiles.filter((profile) => profile.id !== selected.id);
    setProfiles(remaining);
    setSelectedId(defaultId === selected.id ? ENV_PROFILE_ID : defaultId);
    if (defaultId === selected.id) setDefaultId(ENV_PROFILE_ID);
    setTestResult(null);
  };

  const saveProfiles = async () => {
    setSaving(true);
    try {
      const result = await api.saveModelProfiles(userProfiles, defaultId);
      setProfiles(result.profiles.map(editableFromProfile));
      setDefaultId(result.default_profile_id);
      setSelectedId(result.active_profile_id);
      setConfigPath(result.config_path);
      notify.success('模型配置已保存');
    } catch (error) {
      notify.error(`保存模型配置失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const selectForSession = async () => {
    if (!selected) return;
    try {
      await api.selectModelProfile(selected.id);
      notify.success(`当前会话已切换到 ${selected.name}`);
      void loadProfiles();
    } catch (error) {
      notify.error(`切换模型失败: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const testSelected = async () => {
    if (!selected) return;
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await api.testModelProfile(selected));
    } catch (error) {
      setTestResult({
        ok: false,
        message: error instanceof Error ? error.message : String(error),
        latency_ms: 0,
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Space direction="vertical" size="medium" style={{ width: '100%' }}>
      <section className="model-settings-shell">
        <div className="model-settings-list">
          <div className="model-settings-title">
            <span>模型配置</span>
            <Button type="text" icon={<Plus size={16} />} onClick={addProfile} title="新增模型" />
          </div>
          <div className="model-profile-list">
            {profiles.map((profile) => (
              <button
                key={profile.id}
                type="button"
                className={`model-profile-row ${profile.id === selectedId ? 'active' : ''}`}
                onClick={() => {
                  setSelectedId(profile.id);
                  setTestResult(null);
                }}
              >
                <span className={`model-profile-mark ${profile.source === 'env' ? 'env' : 'user'}`}>
                  {profile.id === info?.model_profile_id ? <CheckOne size={13} /> : null}
                </span>
                <span className="model-profile-copy">
                  <strong>{profile.name}</strong>
                  <small>{profile.model || '未设置模型名称'}</small>
                </span>
                {profile.id === defaultId ? <Star size={15} /> : null}
              </button>
            ))}
          </div>
          <div className="model-config-path">
            <Shield size={14} />
            <span>{configPath || '配置文件将在保存后创建'}</span>
          </div>
        </div>

        {selected ? (
          <div className="model-editor-pane">
            <div className="model-editor-head">
              <div>
                <span>{selected.source === 'env' ? '环境默认' : '用户模型'}</span>
                <strong>{selected.name || '未命名模型'}</strong>
              </div>
              <span className={`model-status-pill ${selected.id === info?.model_profile_id ? 'active' : ''}`}>
                {selected.id === info?.model_profile_id ? '当前会话' : '可切换'}
              </span>
            </div>

            <div className="model-form-grid">
              <label>
                <span>展示名称</span>
                <Input
                  value={selected.name}
                  disabled={selected.source === 'env'}
                  onChange={(value) => updateSelected({ name: value })}
                />
              </label>
              <label>
                <span>模型名称</span>
                <Input
                  value={selected.model}
                  disabled={selected.source === 'env'}
                  onChange={(value) => updateSelected({ model: value })}
                />
              </label>
              <label className="wide">
                <span>Base URL</span>
                <Input
                  value={selected.base_url}
                  disabled={selected.source === 'env'}
                  placeholder="https://api.example.com/v1"
                  prefix={<Connection />}
                  onChange={(value) => updateSelected({ base_url: value })}
                />
              </label>
              <label className="wide">
                <span>API Key</span>
                <Input.Password
                  value={selected.api_key || ''}
                  disabled={selected.source === 'env'}
                  placeholder={selected.has_api_key ? `已保存 ${selected.api_key_masked}，留空保持不变` : 'sk-...'}
                  prefix={<Api />}
                  onChange={(value) => updateSelected({ api_key: value })}
                />
              </label>
            </div>

            <div className="model-editor-options">
              <button type="button" className="model-default-toggle" onClick={() => setDefaultId(selected.id)}>
                <Star size={15} />
                {selected.id === defaultId ? '默认模型' : '设为默认'}
              </button>
              <label className="model-enabled-toggle">
                <ArcoSwitch
                  size="small"
                  checked={selected.enabled}
                  disabled={selected.source === 'env'}
                  onChange={(enabled) => updateSelected({ enabled })}
                />
                <span>启用</span>
              </label>
            </div>

            {testResult ? (
              <div className={`model-test-result ${testResult.ok ? 'ok' : 'fail'}`}>
                {testResult.ok ? <CheckOne size={15} /> : <Close size={15} />}
                <span>
                  {testResult.message}
                  {testResult.latency_ms ? ` · ${testResult.latency_ms}ms` : ''}
                </span>
              </div>
            ) : null}

            <div className="model-editor-actions">
              <Button icon={<ExperimentOne size={16} />} loading={testing} onClick={testSelected}>
                测试
              </Button>
              <Button icon={<CheckOne size={16} />} disabled={!selected.enabled} onClick={selectForSession}>
                用于当前会话
              </Button>
              <Button type="primary" icon={<Save size={16} />} loading={saving} onClick={saveProfiles}>
                保存
              </Button>
              <Button
                status="danger"
                type="text"
                icon={<Delete size={16} />}
                disabled={selected.source === 'env'}
                onClick={deleteSelected}
                title="删除模型"
              />
            </div>
          </div>
        ) : null}
      </section>
      <ModeSwitch mode={info?.mode || 'suggest'} onChange={(mode) => void api.mode(mode)} />
      <ActionGrid
        actions={[
          ['保存会话', '/save'],
          ['上下文信息', '/context'],
          ['权限设置', '/perms'],
          ['导出数据', '/export'],
        ]}
        sendCommand={sendCommand}
      />
    </Space>
  );
}
