import { Button, Empty } from '@arco-design/web-react';
import { PreviewClose } from '@icon-park/react';
import { useEffect, useState } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { API_BASE } from '../../api';
import type { FileReadResponse } from '../../types';
import { formatSize } from '../../utils/format';
import { MarkdownLive } from '../MarkdownLive';

const getLanguage = (ext: string) => {
  const langMap: Record<string, string> = {
    '.ts': 'typescript',
    '.tsx': 'tsx',
    '.js': 'javascript',
    '.jsx': 'jsx',
    '.py': 'python',
    '.json': 'json',
    '.css': 'css',
    '.html': 'html',
    '.sh': 'bash',
    '.yaml': 'yaml',
    '.yml': 'yaml',
  };
  return langMap[ext] || 'text';
};

function CodePreview({ text, language, path }: { text: string; language: string; path: string }) {
  const [renderText, setRenderText] = useState('');

  useEffect(() => {
    setRenderText('');
    const timer = window.setTimeout(() => setRenderText(text), 30);
    return () => window.clearTimeout(timer);
  }, [path, text]);

  if (!renderText) {
    return (
      <div className="code-preview-loading">
        <span>Rendering code preview...</span>
      </div>
    );
  }

  return (
    <div className="code-preview-scroll">
      <SyntaxHighlighter
        language={language}
        style={oneLight}
        customStyle={{
          margin: 0,
          minHeight: '100%',
          minWidth: '100%',
          width: 'max-content',
          borderRadius: 0,
          overflow: 'visible',
          fontSize: '12px',
          background: '#fbfcfe',
        }}
        codeTagProps={{
          style: {
            fontFamily: '"Cascadia Code", Consolas, monospace',
          },
        }}
        lineNumberStyle={{
          minWidth: '2.75em',
          paddingRight: '1em',
          color: '#b8c0cc',
          textAlign: 'right',
          userSelect: 'none',
        }}
        showLineNumbers
      >
        {renderText}
      </SyntaxHighlighter>
    </div>
  );
}

export function FilePreviewStage({ preview, onClose }: { preview: FileReadResponse; onClose: () => void }) {
  const rawUrl = `${API_BASE}/api/files/raw?path=${encodeURIComponent(preview.path)}`;
  const imageExts = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']);
  const officeExts = new Set(['.docx', '.pptx', '.xlsx', '.doc', '.ppt', '.xls']);
  const isJson = preview.extension === '.json';
  
  let text = preview.content || '';
  if (isJson) {
    try {
      text = JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      // Keep original text.
    }
  }

  const isHtml = preview.extension === '.html';
  const isCode = preview.kind === 'text' && preview.extension !== '.md' && !isHtml;
  const codeLanguage = getLanguage(preview.extension);

  return (
    <section className="preview-stage">
      <div className="preview-stage-head">
        <div>
          <strong>{preview.path}</strong>
          <small>
            {preview.kind} / {formatSize(preview.size)}
          </small>
        </div>
        <Button size="small" type="text" icon={<PreviewClose />} onClick={onClose} />
      </div>
      <div className={`preview-stage-body ${isCode ? 'preview-stage-body-code' : ''}`}>
        {preview.kind === 'text' && preview.extension === '.md' ? <MarkdownLive content={text} /> : null}
        
        {isCode ? <CodePreview text={text} language={codeLanguage} path={preview.path} /> : null}
        
        {isHtml ? (
          <div className="html-preview-wrap">
            <div className="html-preview-toolbar">
              <span>HTML preview</span>
            </div>
            <iframe
              className="html-preview"
              srcDoc={text}
              sandbox="allow-scripts"
              title={preview.path}
            />
          </div>
        ) : null}

        {preview.kind === 'binary' && imageExts.has(preview.extension) ? <img className="image-preview" src={rawUrl} alt={preview.path} /> : null}
        
        {preview.kind === 'binary' && preview.extension === '.pdf' ? <iframe className="pdf-preview" src={rawUrl} title={preview.path} /> : null}
        
        {(preview.kind === 'binary' && !imageExts.has(preview.extension) && preview.extension !== '.pdf') || officeExts.has(preview.extension) ? (
          <div className="unknown-preview">
            <Empty description={officeExts.has(preview.extension) ? "Office 文档暂不支持直接预览" : "此文件类型无法内嵌预览"} />
            <Button type="primary" href={rawUrl} target="_blank">
              使用本地默认应用打开
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
