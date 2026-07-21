/** Lightweight markdown preview for the Editor live pane. */

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function isSafeImageSrc(src) {
  if (!src) return false;
  const s = src.trim();
  return (
    /^https?:\/\//i.test(s)
    || /^data:image\//i.test(s)
    || s.startsWith('/')
    || s.startsWith('book_images/')
  );
}

/**
 * Escape text, then apply inline markdown. Images become <img> before escaping
 * the surrounding text so data-URIs survive.
 */
function renderInline(text) {
  if (!text) return '';

  const parts = [];
  const imageRe = /!\[([^\]]*)\]\(([^)\s]+)\)/g;
  let last = 0;
  let match;

  while ((match = imageRe.exec(text)) !== null) {
    if (match.index > last) {
      parts.push({ type: 'text', value: text.slice(last, match.index) });
    }
    const [, alt, src] = match;
    if (isSafeImageSrc(src)) {
      parts.push({ type: 'img', alt, src });
    } else {
      parts.push({ type: 'text', value: match[0] });
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push({ type: 'text', value: text.slice(last) });
  }
  if (!parts.length) {
    parts.push({ type: 'text', value: text });
  }

  return parts.map((part) => {
    if (part.type === 'img') {
      return `<img src="${escapeHtml(part.src)}" alt="${escapeHtml(part.alt)}" loading="lazy" class="md-img" />`;
    }
    return escapeHtml(part.value)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*(?!\*)([^*]+?)\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }).join('');
}

/**
 * Render a single line/fragment of markdown to safe inline HTML.
 */
export function renderInlineMarkdown(text) {
  if (!text) return '';
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function stripBlockquote(line) {
  const m = /^(?:>\s?)+(.*)$/.exec(line);
  if (!m) return { quote: false, text: line };
  return { quote: true, text: m[1] };
}

function renderBlocks(lines) {
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
  const flushAll = () => {
    flushPara();
    flushList();
  };

  lines.forEach((rawLine) => {
    const line = String(rawLine).replace(/\s+$/, '');
    const trimmed = line.trim();

    if (trimmed === '') {
      flushPara();
      return;
    }

    if (/^!\[[^\]]*\]\([^)\s]+\)$/.test(trimmed)) {
      flushAll();
      out.push(`<figure>${renderInline(trimmed)}</figure>`);
      return;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushAll();
      out.push(`<h${heading[1].length}>${renderInline(heading[2])}</h${heading[1].length}>`);
      return;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushAll();
      out.push('<hr/>');
      return;
    }

    const ordered = /^(\d+)\.\s+(.+)$/.exec(trimmed);
    if (ordered) {
      flushPara();
      items.push(ordered[2]);
      return;
    }

    const bullet = /^[-*]\s+(.+)$/.exec(trimmed);
    if (bullet) {
      flushPara();
      items.push(bullet[1]);
      return;
    }

    flushList();
    para.push(line);
  });

  flushAll();
  return out.join('');
}

/**
 * Parses structure on raw markdown first (so `>` blockquotes work), then escapes
 * inline text. Supports blockquotes, lists, headings, images, bold/italic.
 */
export function renderMarkdownPreview(text) {
  if (!text) return '';

  const rawLines = String(text).replace(/\r\n/g, '\n').split('\n');
  const segments = [];
  let quoteBuf = [];
  let plainBuf = [];

  const flushQuote = () => {
    if (!quoteBuf.length) return;
    const inner = renderBlocks(quoteBuf);
    segments.push(`<blockquote>${inner || '<p></p>'}</blockquote>`);
    quoteBuf = [];
  };
  const flushPlain = () => {
    if (!plainBuf.length) return;
    segments.push(renderBlocks(plainBuf));
    plainBuf = [];
  };

  rawLines.forEach((raw) => {
    const cleaned = raw.replace(/\s+$/, '');
    const { quote, text } = stripBlockquote(cleaned);
    if (quote) {
      flushPlain();
      quoteBuf.push(text);
      return;
    }
    flushQuote();
    plainBuf.push(text);
  });
  flushQuote();
  flushPlain();

  return segments.join('');
}
