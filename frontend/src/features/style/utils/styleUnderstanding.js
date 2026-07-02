/** Section headers in AI-generated style intelligence (Streamlit parity). */
export const STYLE_UNDERSTANDING_SECTIONS = [
  { name: 'WHAT THIS IS', icon: '📌', tone: 'indigo' },
  { name: 'WHAT I LEARNED', icon: '📖', tone: 'green' },
  { name: 'HOW I WILL WORK', icon: '⚙️', tone: 'amber' },
  { name: 'WHAT I WILL NOT DO', icon: '🚫', tone: 'red' },
];

const ANY_HDR = 'WHAT THIS IS|WHAT I LEARNED|HOW I WILL WORK|WHAT I WILL NOT DO';
const HDR_PREFIX = '(?:#{1,3}\\s*|\\*{1,2})?';
const HDR_SUFFIX = '(?:\\*{1,2})?:?';

/** Remove boilerplate title lines before section parsing. */
export function normalizeUnderstandingText(text) {
  if (!text) return '';
  let t = String(text).trim();
  t = t.replace(/^#\s*STYLE\s+UNDERSTANDING\s+OUTPUT\s*/i, '');
  t = t.replace(/^STYLE\s+UNDERSTANDING\s+OUTPUT\s*/i, '');
  return t.trim();
}

/**
 * Parse structured understanding into sections (matches Streamlit style.py).
 * @returns {{ sections: { name: string, body: string, icon: string, tone: string }[], preamble: string, rawFallback: boolean }}
 */
export function parseStyleUnderstanding(text) {
  if (!text || !String(text).trim()) {
    return { sections: [], preamble: '', rawFallback: false };
  }

  const undText = normalizeUnderstandingText(text);
  const sections = [];

  for (const { name, icon, tone } of STYLE_UNDERSTANDING_SECTIONS) {
    const esc = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pat = new RegExp(
      `${HDR_PREFIX}${esc}${HDR_SUFFIX}\\s*([\\s\\S]*?)(?=${HDR_PREFIX}(?:${ANY_HDR})\\b|$)`,
      'i',
    );
    const match = undText.match(pat);
    const body = match?.[1]?.trim() || '';
    sections.push({ name, body, icon, tone });
  }

  const foundCount = sections.filter((s) => s.body).length;
  if (foundCount > 0) {
    return { sections, preamble: '', rawFallback: false };
  }

  // Fallback: split on ## markdown headings
  const generic = parseGenericMarkdownSections(undText);
  if (generic.length > 0) {
    return { sections: generic, preamble: '', rawFallback: false };
  }

  return { sections: [], preamble: undText, rawFallback: true };
}

function parseGenericMarkdownSections(text) {
  const chunks = text.split(/(?=^#{1,3}\s+)/m).filter(Boolean);
  if (chunks.length <= 1) return [];

  return chunks.map((chunk) => {
    const lines = chunk.trim().split('\n');
    const headLine = lines[0] || '';
    const title = headLine.replace(/^#{1,3}\s*/, '').replace(/\*+/g, '').trim();
    const body = lines.slice(1).join('\n').trim() || headLine.replace(/^#{1,3}\s*[^\n]+\s*/, '').trim();
    const known = STYLE_UNDERSTANDING_SECTIONS.find(
      (s) => s.name.toLowerCase() === title.toLowerCase()
        || title.toLowerCase().includes(s.name.toLowerCase()),
    );
    return {
      name: known?.name || title,
      body,
      icon: known?.icon || '📄',
      tone: known?.tone || 'indigo',
    };
  }).filter((s) => s.name);
}

export function styleUnderstandingText(style) {
  if (!style) return '';
  return style.understanding ?? style.generated_summary ?? '';
}
