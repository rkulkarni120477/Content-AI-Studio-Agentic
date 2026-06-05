import { useMemo } from 'react';
import { renderMarkdownPreview } from '@utils/markdownPreview';
import styles from './MarkdownPreview.module.scss';

export default function MarkdownPreview({ content, className }) {
  const html = useMemo(() => renderMarkdownPreview(content || ''), [content]);
  return (
    <div
      className={`${styles.preview} markdown-content ${className || ''}`}
      dangerouslySetInnerHTML={{ __html: html || '<p class="empty">(empty)</p>' }}
    />
  );
}
