import { Empty, Input, Spin, Tooltip } from '@arco-design/web-react';
import { DocumentFolder, Down, MenuFoldOne, Refresh, Right, Search } from '@icon-park/react';
import type { CSSProperties } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api';
import type { FileEntry, FileListResponse, FileReadResponse } from '../../types';
import { formatSize } from '../../utils/format';
import { notify } from '../../utils/notify';
import { fileIcon } from './fileIcon';

function levelStyle(level: number): CSSProperties {
  return { '--tree-level': level } as CSSProperties;
}

function TreeNode({
  entry,
  level,
  selectedPath,
  onPreview,
}: {
  entry: FileEntry;
  level: number;
  selectedPath?: string;
  onPreview: (file: FileReadResponse) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<FileEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const isDirectory = entry.kind === 'directory';

  const toggleDir = async () => {
    setError('');
    if (expanded) {
      setExpanded(false);
      return;
    }
    if (!children) {
      setLoading(true);
      try {
        const res = await api.listFiles(entry.path);
        setChildren(res.entries);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        notify.error(`Failed to open ${entry.name}: ${message}`);
      } finally {
        setLoading(false);
      }
    }
    setExpanded(true);
  };

  const handleClick = async () => {
    if (isDirectory) {
      await toggleDir();
      return;
    }
    try {
      onPreview(await api.readFile(entry.path));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      notify.error(`Failed to preview ${entry.name}: ${message}`);
    }
  };

  const isSelected = selectedPath === entry.path;

  return (
    <div className="tree-node">
      <Tooltip content={entry.path} position="left">
        <button
          className={`file-node ${isDirectory ? 'file-node-directory' : 'file-node-file'} ${isSelected ? 'selected' : ''}`}
          style={levelStyle(level)}
          onClick={() => void handleClick()}
        >
          <span className="file-node-disclosure">{isDirectory ? expanded ? <Down size={13} /> : <Right size={13} /> : null}</span>
          <span className="file-node-icon">{isDirectory ? <DocumentFolder size={16} /> : fileIcon(entry.name)}</span>
          <span className="file-node-label">{entry.name}</span>
          {entry.kind === 'file' ? <small className="file-node-size">{formatSize(entry.size)}</small> : null}
        </button>
      </Tooltip>
      {expanded && loading ? (
        <div className="tree-inline-state" style={levelStyle(level + 1)}>
          <Spin size={12} /> Loading...
        </div>
      ) : null}
      {expanded && error ? (
        <div className="tree-inline-state tree-error" style={levelStyle(level + 1)}>
          {error}
        </div>
      ) : null}
      {expanded && children ? (
        <div className="tree-children" style={levelStyle(level)}>
          <div className="tree-indent-guide" />
          {children.length === 0 ? (
            <div className="tree-inline-state" style={levelStyle(level + 1)}>
              Empty folder
            </div>
          ) : null}
          {children.map((child) => (
            <TreeNode key={child.path} entry={child} level={level + 1} selectedPath={selectedPath} onPreview={onPreview} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function WorkspaceTree({
  collapsed,
  selectedPath,
  onToggle,
  onPreview,
}: {
  collapsed: boolean;
  selectedPath?: string;
  onToggle: () => void;
  onPreview: (file: FileReadResponse) => void;
}) {
  const [files, setFiles] = useState<FileListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  const visibleEntries = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return files?.entries || [];
    return (files?.entries || []).filter((entry) => entry.name.toLowerCase().includes(needle) || entry.path.toLowerCase().includes(needle));
  }, [files?.entries, query]);

  const loadFiles = async () => {
    setLoading(true);
    setError('');
    try {
      setFiles(await api.workspace());
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      notify.error(`Failed to load workspace: ${message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadFiles();
  }, []);

  return (
    <aside className={`workspace ${collapsed ? 'collapsed' : ''}`}>
      <div className="workspace-top">
        {!collapsed ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <button className="icon-button workspace-toggle" onClick={onToggle}>
                <MenuFoldOne />
              </button>
              <strong>工作区</strong>
            </div>
            <button className="icon-button workspace-refresh" onClick={() => void loadFiles()}>
              <Refresh />
            </button>
          </>
        ) : null}
      </div>
      {!collapsed ? (
        <div className="workspace-body">
          <Input.Search className="workspace-search" value={query} onChange={setQuery} placeholder="搜索文件" prefix={<Search />} />
          <div className="file-tree">
            {loading ? <Spin /> : null}
            {!loading && error ? <Empty description={error} /> : null}
            {!loading && !error && visibleEntries.length === 0 ? <div className="empty-file-result">没有匹配的文件</div> : null}
            {!error
              ? visibleEntries.map((entry) => (
                  <TreeNode key={entry.path} entry={entry} level={0} selectedPath={selectedPath} onPreview={onPreview} />
                ))
              : null}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
