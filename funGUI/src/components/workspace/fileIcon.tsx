import { Code, FileCode, FilePdf, FileText } from '@icon-park/react';

export function fileIcon(name: string) {
  const lower = name.toLowerCase();
  if (lower.endsWith('.pdf')) return <FilePdf />;
  if (lower.endsWith('.md') || lower.endsWith('.txt')) return <FileText />;
  if (lower.endsWith('.py') || lower.endsWith('.tsx') || lower.endsWith('.ts') || lower.endsWith('.json')) return <Code />;
  return <FileCode />;
}
