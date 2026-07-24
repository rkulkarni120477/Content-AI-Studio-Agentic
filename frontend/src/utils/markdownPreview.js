/**
 * Markdown rendering for content previews across the app (CDD, Blueprint,
 * Generate, Feedback, …).
 *
 * `renderMarkdownPreview` now delegates to the shared, sanitized Markdown→HTML
 * converter (`@utils/markdownHtml`) so rich inline-HTML islands — video embeds,
 * sized/aligned images, underline — render correctly everywhere the editor does,
 * instead of showing up as raw `<iframe>`/`<figure>` tags. `breaks: true` keeps
 * the legacy line-break look these previews had before.
 *
 * `renderInlineMarkdown` stays a lightweight, escape-first inline renderer for
 * single-line snippets (e.g. per-item text in regenerate panels) — it is NOT a
 * block renderer and intentionally does not pass HTML through.
 */
import { mdToHtml } from './markdownHtml';

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Render a single line/fragment of markdown to safe inline HTML (bold + code).
 */
export function renderInlineMarkdown(text) {
  if (!text) return '';
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

/**
 * Render full markdown block content to sanitized HTML for preview panes.
 */
export function renderMarkdownPreview(text) {
  return mdToHtml(text || '', { breaks: true });
}
