import { useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button, Tooltip, message } from 'antd';
import { CopyOutlined, CheckOutlined } from '@ant-design/icons';

interface MarkdownRendererProps {
  content: string;
  streaming?: boolean;
}

export default function MarkdownRenderer({ content, streaming }: MarkdownRendererProps) {
  return (
    <div className="markdown-body" style={{ lineHeight: 1.7 }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code: CodeBlock,
          a: ({ href, children, ...props }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div style={{ overflowX: 'auto', margin: '8px 0' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th style={{ border: '1px solid #d9d9d9', padding: '6px 12px', background: '#fafafa', fontWeight: 600, textAlign: 'left' }}>{children}</th>
          ),
          td: ({ children }) => (
            <td style={{ border: '1px solid #d9d9d9', padding: '6px 12px' }}>{children}</td>
          ),
          p: ({ children }) => <p style={{ margin: '4px 0' }}>{children}</p>,
          ul: ({ children }) => <ul style={{ paddingLeft: 20, margin: '4px 0' }}>{children}</ul>,
          ol: ({ children }) => <ol style={{ paddingLeft: 20, margin: '4px 0' }}>{children}</ol>,
          blockquote: ({ children }) => (
            <blockquote style={{ borderLeft: '3px solid #667eea', margin: '8px 0', padding: '4px 12px', color: '#666', background: '#f9f9ff' }}>
              {children}
            </blockquote>
          ),
          hr: () => <hr style={{ border: 'none', borderTop: '1px solid #e8e8e8', margin: '12px 0' }} />,
        }}
      >
        {content}
      </ReactMarkdown>
      {streaming && (
        <span className="streaming-cursor" style={{
          display: 'inline-block',
          width: 2,
          height: 16,
          background: '#667eea',
          marginLeft: 2,
          verticalAlign: 'text-bottom',
          animation: 'blink 1s infinite',
        }} />
      )}
    </div>
  );
}

/* Code block with copy button + language label */
function CodeBlock({ className, children, ...props }: any) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || '');
  const lang = match ? match[1] : '';
  const codeText = String(children).replace(/\n$/, '');
  const isInline = !className && !codeText.includes('\n');

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(codeText).then(() => {
      setCopied(true);
      message.success('已复制');
      setTimeout(() => setCopied(false), 2000);
    });
  }, [codeText]);

  if (isInline) {
    return (
      <code style={{
        background: '#f0f0f0',
        padding: '1px 6px',
        borderRadius: 3,
        fontSize: '0.9em',
        fontFamily: "'SF Mono', Monaco, Menlo, Consolas, monospace",
      }} {...props}>
        {children}
      </code>
    );
  }

  return (
    <div style={{ position: 'relative', margin: '8px 0' }}>
      {lang && (
        <div style={{
          background: '#2d2d2d',
          color: '#aaa',
          fontSize: 11,
          padding: '2px 12px',
          borderRadius: '6px 6px 0 0',
          fontFamily: 'monospace',
        }}>
          {lang}
        </div>
      )}
      <Tooltip title={copied ? '已复制' : '复制代码'}>
        <Button
          type="text"
          size="small"
          icon={copied ? <CheckOutlined style={{ color: '#52c41a' }} /> : <CopyOutlined />}
          onClick={handleCopy}
          style={{
            position: 'absolute',
            top: lang ? 24 : 4,
            right: 4,
            zIndex: 1,
            color: '#999',
          }}
        />
      </Tooltip>
      <pre style={{
        background: '#1e1e1e',
        color: '#d4d4d4',
        padding: '12px 16px',
        borderRadius: lang ? '0 0 6px 6px' : 6,
        overflow: 'auto',
        fontSize: 13,
        lineHeight: 1.5,
        margin: 0,
      }}>
        <code className={className} style={{ fontFamily: "'SF Mono', Monaco, Menlo, Consolas, monospace" }}>
          {children}
        </code>
      </pre>
    </div>
  );
}
