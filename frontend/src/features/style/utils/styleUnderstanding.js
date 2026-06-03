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

/**
 * Parse structured understanding into sections (matches Streamlit style.py).
 * @returns {{ sections: { name: string, body: string, icon: string, tone: string }[], rawFallback: boolean }}
 */
export function parseStyleUnderstanding(text) {
  if (!text || !String(text).trim()) {
    return { sections: [], rawFallback: false };
  }

  const undText = String(text);
  const sections = [];

  for (const { name, icon, tone } of STYLE_UNDERSTANDING_SECTIONS) {
    const esc = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pat = new RegExp(
      `${HDR_PREFIX}${esc}${HDR_SUFFIX}\\s*([\\s\\S]*?)(?=${HDR_PREFIX}(?:${ANY_HDR})|$)`,
      'i',
    );
    const match = undText.match(pat);
    if (match?.[1]?.trim()) {
      sections.push({ name, body: match[1].trim(), icon, tone });
    }
  }

  return {
    sections,
    rawFallback: sections.length === 0,
  };
}
