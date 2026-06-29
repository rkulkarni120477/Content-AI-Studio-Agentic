/** Lightweight markdown preview matching Streamlit st.markdown rendering for editor preview. */

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderInline(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

/**
 * Block-based renderer: groups consecutive bullets into a single <ul>, emits
 * <p> blocks on blank-line boundaries, and lets the .markdown-content CSS own
 * the vertical spacing (no <br/> between every line). Supports h1–h4, bullet
 * lists, horizontal rules, bold, and inline code.
 */
export function renderMarkdownPreview(text) {
  if (!text) return '';
  const lines = escapeHtml(text).split('\n');

  const out = [];
  let para = [];
  let items = [];

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${para.map(renderInline).join('<br/>')}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (items.length) {
      out.push(`<ul>${items.map((it) => `<li>${renderInline(it)}</li>`).join('')}</ul>`);
      items = [];
    }
  };
  const flushAll = () => { flushPara(); flushList(); };

  lines.forEach((raw) => {
    const line = raw.replace(/\s+$/, '');
    const trimmed = line.trim();

    // Blank line: end the current paragraph, but keep an open list intact so
    // bullets separated by blank lines stay in one <ul>.
    if (trimmed === '') {
      flushPara();
      return;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushAll();
      const level = heading[1].length;
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      return;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushAll();
      out.push('<hr/>');
      return;
    }

    const bullet = /^[-*]\s+(.+)$/.exec(trimmed);
    if (bullet) {
      flushPara();
      items.push(bullet[1]);
      return;
    }

    // Plain text line: a preceding list ends here; soft-wrap within a paragraph.
    flushList();
    para.push(line);
  });

  flushAll();
  return out.join('');
}
