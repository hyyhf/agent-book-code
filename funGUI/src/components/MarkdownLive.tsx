import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';

const remarkPlugins = [remarkGfm];
const highlightPlugins = [rehypeHighlight];

export const MarkdownLive = memo(function MarkdownLive({ content, streaming = false }: { content: string; streaming?: boolean }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={highlightPlugins}>
        {content || ''}
      </ReactMarkdown>
    </div>
  );
});
