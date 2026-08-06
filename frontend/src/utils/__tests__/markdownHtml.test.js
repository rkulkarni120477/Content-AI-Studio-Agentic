// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { mdToHtml, htmlToMd, sanitizeHtml, normalizeMarkdownTables } from '../markdownHtml';

/** Normalize whitespace so structural round-trip comparisons ignore cosmetic gaps. */
const norm = (s) => s.replace(/\r\n/g, '\n').replace(/\n{2,}/g, '\n').trim();

describe('mdToHtml', () => {
  it('renders headings, bold, italic and inline code', () => {
    const html = mdToHtml('# Title\n\nSome **bold** and *italic* and `code`.');
    expect(html).toContain('<h1>Title</h1>');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<em>italic</em>');
    expect(html).toContain('<code>code</code>');
  });

  it('renders bullet and numbered lists', () => {
    expect(mdToHtml('- a\n- b')).toContain('<ul>');
    expect(mdToHtml('1. a\n2. b')).toContain('<ol>');
  });

  it('renders blockquotes', () => {
    expect(mdToHtml('> quoted')).toContain('<blockquote>');
  });

  it('renders GFM tables', () => {
    const html = mdToHtml('| A | B |\n| --- | --- |\n| 1 | 2 |');
    expect(html).toContain('<table>');
    expect(html).toContain('<th>A</th>');
    expect(html).toContain('<td>1</td>');
  });

  it('renders images with a safe src', () => {
    const html = mdToHtml('![alt text](https://example.com/x.png)');
    expect(html).toContain('<img');
    expect(html).toContain('src="https://example.com/x.png"');
    expect(html).toContain('alt="alt text"');
  });

  it('returns empty string for empty/blank input', () => {
    expect(mdToHtml('')).toBe('');
    expect(mdToHtml('   \n  ')).toBe('');
  });

  it('renders a table whose delimiter row is one column short (LLM output)', () => {
    // Real shape from the DLU Worksheet 4: 10-col header, 9-col delimiter.
    const md = [
      '| Day | Day Title | ACS Code(s) | Concept Type | Concept Scope | Content Summary | Formative Assessment | Projects/Activities | Source Completeness | Flags |',
      '|---|---|---|---|---|---|---|---|---|',
      '| 1 | Aircraft Drawings | AM.I.B.K1 | Knowledge | Intro | Overview | Quiz 1 | Project 2-1 | Complete | |',
    ].join('\n');
    const html = mdToHtml(md);
    expect(html).toContain('<table>');
    expect(html).toContain('<th>Day</th>');
    expect(html).toContain('<td>Aircraft Drawings</td>');
    expect(html).not.toContain('| Day |'); // no raw pipes left over
  });
});

describe('normalizeMarkdownTables', () => {
  it('pads a short delimiter row to the header column count', () => {
    const md = [
      '| A | B | C | D | E | F |',
      '|---|---|---|---|---|',
      '| 1 | 2 | 3 | 4 | 5 | 6 |',
    ].join('\n');
    const out = normalizeMarkdownTables(md).split('\n');
    expect(out[1]).toBe('| --- | --- | --- | --- | --- | --- |');
  });

  it('leaves a well-formed table byte-for-byte unchanged', () => {
    const md = '| A | B |\n| --- | --- |\n| 1 | 2 |';
    expect(normalizeMarkdownTables(md)).toBe(md);
  });

  it('does not touch non-table content that contains pipes', () => {
    const md = 'Use a | b syntax in prose.\nAnother line.';
    expect(normalizeMarkdownTables(md)).toBe(md);
  });
});

describe('sanitization', () => {
  it('strips <script> tags', () => {
    expect(sanitizeHtml('<p>ok</p><script>alert(1)</script>')).not.toContain('script');
  });

  it('strips event-handler attributes', () => {
    const clean = sanitizeHtml('<img src="x" onerror="alert(1)">');
    expect(clean).not.toContain('onerror');
  });

  it('drops javascript: URLs on links', () => {
    const clean = sanitizeHtml('<a href="javascript:alert(1)">x</a>');
    expect(clean).not.toContain('javascript:');
  });

  it('keeps a YouTube embed iframe', () => {
    const clean = sanitizeHtml('<iframe src="https://www.youtube.com/embed/abc123"></iframe>');
    expect(clean).toContain('<iframe');
    expect(clean).toContain('https://www.youtube.com/embed/abc123');
    expect(clean).toContain('allowfullscreen');
  });

  it('drops a non-YouTube iframe', () => {
    expect(sanitizeHtml('<iframe src="https://evil.example.com/x"></iframe>')).not.toContain('iframe');
  });

  it('drops a YouTube iframe that is not an /embed/ path', () => {
    expect(sanitizeHtml('<iframe src="https://www.youtube.com/watch?v=abc"></iframe>')).not.toContain('iframe');
  });

  it('strips an onload handler from an allowlisted iframe', () => {
    const clean = sanitizeHtml('<iframe src="https://www.youtube.com/embed/x" onload="evil()"></iframe>');
    expect(clean).toContain('<iframe');
    expect(clean).not.toContain('onload="evil');
  });

  it('forces safe target/rel on links', () => {
    const clean = sanitizeHtml('<a href="https://example.com">x</a>');
    expect(clean).toContain('rel="noopener noreferrer nofollow"');
    expect(clean).toContain('target="_blank"');
  });

  it('keeps only text-align in style attributes', () => {
    const clean = sanitizeHtml('<p style="text-align: center; position: fixed">x</p>');
    expect(clean).toContain('text-align: center');
    expect(clean).not.toContain('position');
  });

  it('drops non-align styles entirely', () => {
    const clean = sanitizeHtml('<p style="color: red">x</p>');
    expect(clean).not.toContain('style');
  });
});

describe('htmlToMd', () => {
  it('serializes headings and emphasis to canonical markdown', () => {
    expect(htmlToMd('<h2>Title</h2>')).toBe('## Title');
    expect(htmlToMd('<p><strong>b</strong> <em>i</em></p>')).toBe('**b** *i*');
  });

  it('uses "-" as the bullet marker', () => {
    expect(htmlToMd('<ul><li>a</li><li>b</li></ul>')).toBe('- a\n- b');
  });

  it('preserves underline as an inline-HTML island', () => {
    expect(htmlToMd('<p><u>under</u></p>')).toContain('<u>under</u>');
  });

  it('preserves text alignment as an inline-HTML island', () => {
    const md = htmlToMd('<p style="text-align: center">centered</p>');
    expect(md).toContain('text-align: center');
    expect(md).toContain('centered');
  });

  it('serializes tables back to GFM', () => {
    const md = htmlToMd('<table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>');
    expect(md).toContain('| A |');
    expect(md).toContain('| 1 |');
  });
});

describe('images (Phase 2)', () => {
  it('keeps a plain image as Markdown', () => {
    expect(htmlToMd('<img src="https://x.com/a.png" alt="cat">')).toBe('![cat](https://x.com/a.png)');
  });

  it('preserves image width as an inline-HTML island', () => {
    const md = htmlToMd('<img src="https://x.com/a.png" alt="cat" width="300">');
    expect(md).toContain('<img');
    expect(md).toContain('width="300"');
  });

  it('preserves an aligned image figure', () => {
    const html = '<figure style="text-align: center"><img src="https://x.com/a.png" alt="cat"></figure>';
    const md = htmlToMd(html);
    expect(md).toContain('<figure');
    expect(md).toContain('text-align: center');
    expect(md).toContain('<img');
  });

  it('sanitization keeps width and figure text-align but drops unsafe bits', () => {
    const clean = sanitizeHtml('<figure style="text-align: right; position: fixed"><img src="https://x.com/a.png" width="200" onerror="x()"></figure>');
    expect(clean).toContain('text-align: right');
    expect(clean).toContain('width="200"');
    expect(clean).not.toContain('position');
    expect(clean).not.toContain('onerror');
  });

  it('an image figure round-trips md → html → md', () => {
    const md = '<figure style="text-align: center"><img src="https://x.com/a.png" alt="cat" width="300"></figure>';
    const round = htmlToMd(mdToHtml(md));
    expect(round).toContain('<figure');
    expect(round).toContain('text-align: center');
    expect(round).toContain('width="300"');
  });
});

describe('embeds (Phase 3)', () => {
  it('a YouTube embed round-trips md → html → md', () => {
    const md = '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>';
    const round = htmlToMd(mdToHtml(md));
    expect(round).toContain('<iframe');
    expect(round).toContain('https://www.youtube.com/embed/dQw4w9WgXcQ');
  });

  it('a non-YouTube iframe island is stripped on the way through', () => {
    const md = '<iframe src="https://evil.example.com/x"></iframe>';
    const round = htmlToMd(mdToHtml(md));
    expect(round).not.toContain('iframe');
  });
});

describe('round-trip stability (md → html → md)', () => {
  const samples = [
    '# Lesson Title\n\nAn introductory paragraph explaining the topic.',
    '## Objectives\n\n- Understand X\n- Apply Y\n- Evaluate Z',
    '1. First step\n2. Second step\n3. Third step',
    '> A key takeaway worth highlighting.',
    'Text with **bold**, *italic*, and `inline code`.',
    '### Section\n\nPara one.\n\nPara two with a [link](https://example.com).',
    '| Term | Definition |\n| --- | --- |\n| API | Application Programming Interface |',
  ];

  samples.forEach((md, i) => {
    it(`sample #${i + 1} survives a round-trip`, () => {
      const round = htmlToMd(mdToHtml(md));
      expect(norm(round)).toBe(norm(md));
    });
  });
});
