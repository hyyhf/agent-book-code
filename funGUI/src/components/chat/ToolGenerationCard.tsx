import { Code, Right } from '@icon-park/react';
import type { ChatItem } from '../../types';
import { compactJson } from '../../utils/format';

function formatParameterBlock(value: unknown): string {
  return compactJson(value) || String(value);
}

function splitConcatenatedJsonValues(content: string): unknown[] {
  const values: unknown[] = [];
  let start = -1;
  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];

    if (start === -1) {
      if (/\s/.test(char)) continue;
      if (char !== '{' && char !== '[') return [];
      start = index;
      depth = 1;
      continue;
    }

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
    } else if (char === '{' || char === '[') {
      depth += 1;
    } else if (char === '}' || char === ']') {
      depth -= 1;
      if (depth === 0) {
        try {
          values.push(JSON.parse(content.slice(start, index + 1)) as unknown);
        } catch {
          return [];
        }
        start = -1;
      }
    }
  }

  return start === -1 ? values : [];
}

function parseParameterBlocks(content: string): string[] {
  const trimmed = content.trim();
  if (!trimmed) return ['等待参数...'];

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (Array.isArray(parsed)) return parsed.map(formatParameterBlock);
    if (parsed && typeof parsed === 'object') {
      const record = parsed as Record<string, unknown>;
      const nestedCalls = record.tool_calls || record.calls || record.arguments;
      if (Array.isArray(nestedCalls)) return nestedCalls.map(formatParameterBlock);
      return [compactJson(record)];
    }
  } catch {
    const splitValues = splitConcatenatedJsonValues(trimmed);
    if (splitValues.length > 1) return splitValues.map(formatParameterBlock);
  }

  return [trimmed];
}

export function ToolGenerationCard({ item }: { item: Extract<ChatItem, { type: 'tool_gen' }> }) {
  const parameterBlocks = parseParameterBlocks(item.content);
  const comboOffset = item.index || 0;
  const isSlowGeneration = item.name === 'tool_write_file' || item.name === 'tool_replace_in_file';
  const isGenerating = isSlowGeneration && !item.done;

  return (
    <details className="tool-card tool-event-card tool-gen-card">
      <summary className="tool-card-head">
        <div className="tool-card-title">
          <Code size={15} />
          {isGenerating ? <span className="tool-gen-spinner" aria-hidden="true" /> : null}
          <strong className={isGenerating ? 'tool-status-running' : ''}>{item.name ? `${item.name}工具参数${isGenerating ? '生成中' : '已生成'}` : '工具参数已生成'}</strong>
        </div>
        <div className="tool-card-actions">
          <span className="tool-combo-badge">{parameterBlocks.length > 1 ? `${parameterBlocks.length}组参数` : `参数组合 #${comboOffset + 1}`}</span>
          <Right className="tool-card-chevron" size={14} />
        </div>
      </summary>
      <div className="tool-detail-body">
        {parameterBlocks.map((block, index) => (
          <div className="tool-section" key={`${item.id}-${index}`}>
            <span>参数组合 #{parameterBlocks.length > 1 ? index + 1 : comboOffset + 1}</span>
            <pre>{block}</pre>
          </div>
        ))}
      </div>
    </details>
  );
}
