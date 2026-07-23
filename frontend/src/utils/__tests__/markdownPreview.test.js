// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { renderMarkdownPreview, renderInlineMarkdown } from '../markdownPreview';

describe('renderMarkdownPreview (shared preview renderer)', () => {
  it('renders a YouTube embed island instead of showing raw tags', () => {
    const html = renderMarkdownPreview('<iframe src="https://www.youtube.com/embed/abc"></iframe>');
    expect(html).toContain('<iframe');
    expect(html).toContain('youtube.com/embed/abc');
  });

  it('renders an aligned/sized image figure', () => {
    const html = renderMarkdownPreview('<figure style="text-align: center"><img src="https://x/a.png" width="300"></figure>');
    expect(html).toContain('<figure');
    expect(html).toContain('<img');
  });

  it('still renders ordinary markdown (headings, lists, tables)', () => {
    expect(renderMarkdownPreview('## Title')).toContain('<h2>Title</h2>');
    expect(renderMarkdownPreview('- a\n- b')).toContain('<ul>');
    expect(renderMarkdownPreview('| A |\n| --- |\n| 1 |')).toContain('<table>');
  });

  it('sanitizes dangerous content', () => {
    expect(renderMarkdownPreview('<script>alert(1)</script>')).not.toContain('<script');
    expect(renderMarkdownPreview('<iframe src="https://evil.com/x"></iframe>')).not.toContain('iframe');
  });

  it('empty input renders empty', () => {
    expect(renderMarkdownPreview('')).toBe('');
    expect(renderMarkdownPreview(null)).toBe('');
  });
});

describe('renderInlineMarkdown (unchanged inline renderer)', () => {
  it('escapes HTML and applies bold/code', () => {
    expect(renderInlineMarkdown('**b** `c`')).toBe('<strong>b</strong> <code>c</code>');
    expect(renderInlineMarkdown('<b>x</b>')).toBe('&lt;b&gt;x&lt;/b&gt;');
  });
});
