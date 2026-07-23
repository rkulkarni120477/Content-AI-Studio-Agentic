/**
 * Markdown ⇄ HTML bridge for the unified block editor.
 *
 * The canonical stored format for a block stays **Markdown** (`block.content`).
 * The rich-text editor and the preview both work in HTML, so this module owns
 * the two conversions and the single sanitization allowlist used on both sides:
 *
 *   mdToHtml(markdown)  → sanitized HTML   (load into editor / render preview)
 *   htmlToMd(html)      → Markdown          (serialize editor output for save)
 *
 * Anything Markdown cannot express natively (underline, text alignment, and — in
 * later phases — captioned images and embeds) is preserved as a small, sanitized
 * inline-HTML island inside the Markdown. marked passes that HTML through and the
 * turndown rules below keep it verbatim, so it round-trips without loss.
 *
 * IMPORTANT: conversions here are only ever applied to content the user has
 * actually edited. On load we never re-serialize, so an unopened/unedited block's
 * stored Markdown is left byte-for-byte untouched (protects autosave, version
 * diffs, and line-index-based item regeneration).
 */
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import TurndownService from 'turndown';
import { gfm } from 'turndown-plugin-gfm';

// ---------------------------------------------------------------------------
// Sanitization allowlist (Phase 1). Extended in later phases for media/embeds.
// ---------------------------------------------------------------------------

const ALLOWED_TAGS = [
  'p', 'br', 'span', 'div',
  'strong', 'b', 'em', 'i', 'u', 's', 'del', 'mark', 'sub', 'sup',
  'code', 'pre',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li',
  'blockquote', 'hr',
  'a', 'img',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
  'figure', 'figcaption',
  'iframe',
];

const ALLOWED_ATTR = [
  'href', 'title', 'target', 'rel',
  'src', 'alt', 'width', 'height',
  'style', 'align', 'colspan', 'rowspan',
  'allow', 'allowfullscreen', 'frameborder',
];

// Only these hosts may appear as an <iframe src>. Everything else is dropped.
const ALLOWED_EMBED_HOSTS = [
  'www.youtube.com', 'youtube.com',
  'www.youtube-nocookie.com', 'youtube-nocookie.com',
];

/** True only for an https YouTube /embed/ URL. */
export function isAllowedEmbedSrc(src) {
  try {
    const u = new URL(String(src), 'https://x.invalid');
    return u.protocol === 'https:'
      && ALLOWED_EMBED_HOSTS.includes(u.hostname)
      && u.pathname.startsWith('/embed/');
  } catch {
    return false;
  }
}

// Only text-align is permitted through the `style` attribute — everything else
// (positioning, url(), expression(), etc.) is dropped to keep style XSS-safe.
const TEXT_ALIGN_DECL = /text-align\s*:\s*(left|right|center|justify)/i;

let hooksInstalled = false;

function installHooks() {
  if (hooksInstalled) return;

  // Drop any <iframe> whose src is not an allowlisted embed host, before its
  // attributes are even processed.
  DOMPurify.addHook('uponSanitizeElement', (node, data) => {
    if (data.tagName === 'iframe') {
      const src = node.getAttribute && node.getAttribute('src');
      if (!isAllowedEmbedSrc(src)) {
        node.parentNode?.removeChild(node);
      }
    }
  });

  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    // Lock down allowlisted iframes to a fixed, safe attribute shape.
    if (node.tagName === 'IFRAME') {
      node.setAttribute('allowfullscreen', 'true');
      node.setAttribute('frameborder', '0');
      node.setAttribute('loading', 'lazy');
      node.setAttribute(
        'allow',
        'accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture',
      );
      node.removeAttribute('style');
      node.removeAttribute('onload');
    }
    // Harden links: force safe target/rel on anything that opens a new context.
    if (node.tagName === 'A' && node.hasAttribute('href')) {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer nofollow');
    }
    // Reduce `style` to a single safe text-align declaration, or strip it.
    if (node.hasAttribute('style')) {
      const match = TEXT_ALIGN_DECL.exec(node.getAttribute('style') || '');
      if (match) {
        node.setAttribute('style', `text-align: ${match[1].toLowerCase()}`);
      } else {
        node.removeAttribute('style');
      }
    }
  });

  hooksInstalled = true;
}

/** Sanitize an untrusted HTML string against the shared allowlist. */
export function sanitizeHtml(html) {
  installHooks();
  // NOTE: we deliberately do NOT set a custom ALLOWED_URI_REGEXP — DOMPurify's
  // default already blocks javascript:/vbscript: on href/src while allowing
  // https, relative paths, and data:image. A custom regexp is also applied to
  // non-URI attributes (e.g. width), which would strip legitimate values.
  return DOMPurify.sanitize(html || '', {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    // iframe is NOT forbidden here — it is allowlisted above and constrained to
    // YouTube-embed src by the uponSanitizeElement hook.
    FORBID_TAGS: ['script', 'style', 'object', 'embed', 'form', 'input'],
    FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover'],
    ALLOW_DATA_ATTR: false,
  });
}

// ---------------------------------------------------------------------------
// Markdown → HTML
// ---------------------------------------------------------------------------

marked.setOptions({
  gfm: true,      // tables, strikethrough, autolinks
  breaks: false,  // a single newline is not a hard break (matches source style)
  headerIds: false,
  mangle: false,
});

/**
 * Convert Markdown (with optional inline-HTML islands) to sanitized HTML for the
 * editor / preview. Returns '' for empty input.
 */
export function mdToHtml(markdown) {
  if (!markdown || !String(markdown).trim()) return '';
  const rawHtml = marked.parse(String(markdown));
  return sanitizeHtml(rawHtml);
}

// ---------------------------------------------------------------------------
// HTML → Markdown
// ---------------------------------------------------------------------------

const turndown = new TurndownService({
  headingStyle: 'atx',          // "## Heading"
  hr: '---',
  bulletListMarker: '-',
  codeBlockStyle: 'fenced',
  emDelimiter: '*',
  strongDelimiter: '**',
  linkStyle: 'inlined',
});

turndown.use(gfm);

// Underline has no Markdown equivalent — keep it as an inline-HTML island.
turndown.addRule('underline', {
  filter: ['u'],
  replacement: (content) => (content ? `<u>${content}</u>` : ''),
});

// Embedded iframes (YouTube) have no Markdown form — keep them as sanitized
// inline-HTML islands. Sanitization has already dropped any non-allowlisted src.
turndown.addRule('iframeEmbed', {
  filter: 'iframe',
  replacement: (_content, node) => `\n\n${node.outerHTML}\n\n`,
});

// Images with a caption, width, or alignment can't be expressed in Markdown, so
// keep the whole <figure> as an inline-HTML island (already sanitized upstream).
turndown.addRule('imageFigure', {
  filter: (node) => node.nodeName === 'FIGURE' && !!node.querySelector('img'),
  replacement: (_content, node) => `\n\n${node.outerHTML}\n\n`,
});

// A bare <img> carrying width keeps that attribute by staying HTML; a plain
// <img> (src/alt only) falls through to turndown's default `![alt](src)`.
turndown.addRule('imageWithWidth', {
  filter: (node) => node.nodeName === 'IMG' && !!node.getAttribute('width'),
  replacement: (_content, node) => node.outerHTML,
});

// Text-aligned block elements can't be expressed in Markdown either — emit the
// element as a sanitized inline-HTML island so alignment survives the round-trip.
turndown.addRule('alignedBlock', {
  filter: (node) => (
    ['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6'].includes(node.nodeName)
    && /text-align\s*:/i.test(node.getAttribute('style') || '')
  ),
  replacement: (content, node) => {
    const tag = node.nodeName.toLowerCase();
    const align = (TEXT_ALIGN_DECL.exec(node.getAttribute('style') || '') || [])[1];
    if (!align) return content;
    return `\n\n<${tag} style="text-align: ${align.toLowerCase()}">${content}</${tag}>\n\n`;
  },
});

/**
 * Convert editor HTML back to Markdown for saving. Sanitizes first so no unsafe
 * markup can be persisted, then serializes to the canonical Markdown form.
 */
export function htmlToMd(html) {
  if (!html || !String(html).trim()) return '';
  const clean = sanitizeHtml(html);
  const md = turndown.turndown(clean);
  return md
    // turndown pads list markers ("-   x", "1.  x"); collapse to a single space
    // so edited lists match the source style and don't create phantom diffs.
    // Leading indentation (nesting) is preserved.
    .replace(/^(\s*)([-*+])[ \t]+/gm, '$1$2 ')
    .replace(/^(\s*)(\d+\.)[ \t]+/gm, '$1$2 ')
    // Collapse the 3+ blank lines turndown emits around HTML islands to the
    // standard paragraph gap, and trim trailing whitespace.
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ \t]+$/gm, '')
    .trim();
}
