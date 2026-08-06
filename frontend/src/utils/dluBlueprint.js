/**
 * DLU (day-based) Block Blueprint parsing & splicing.
 *
 * A DLU day blueprint is NOT organised as `## ` sections like a module blueprint.
 * It is a short metadata header (Day Number / Day Type / Topic / Codes) followed
 * by the DLU parts. The DLU generator has emitted those parts in a few shapes:
 *
 *   • numbered bold under a heading:   "### DLU Outline" then "1. **Today's Mission**"
 *   • non-numbered bold under heading: "### DLU Outline" then "**Today's Mission**"
 *   • bold-with-colon, no heading:     "**Today's Mission:**"
 *
 * The Blueprint page's standard `## `-section parser finds nothing in any of
 * these, so no per-section Save/Regenerate controls appear. These helpers treat
 * each DLU part as a section (plus an "Overview" for the header) and splice an
 * edited/regenerated part back in without disturbing the others.
 *
 * Detection is gated to the canonical DLU part NAMES, so it never mistakes an
 * ordinary bold label (e.g. "**Topic Label:**") for a part, and a standard module
 * blueprint (which has none of these names as bold headers, and uses `## `
 * sections) returns false — this is purely additive to the DLU path.
 */

// The canonical DLU parts, in order. A bold header line only starts a section if
// its (cleaned) title starts with one of these.
const DLU_TITLE_RES = [
  /^today'?s mission\b/i,
  /^learn\s*it\b/i,
  /^quick check\b/i,
  /^up next\b/i,          // "Up Next" / "Up Next in Class"
  /^(?:day reflection|reflection)\b/i,
];

// A line that is ENTIRELY a bold title, optionally with a leading number/bullet
// and an optional trailing colon — e.g. "1. **Today's Mission**",
// "**Today's Mission**", "**Today's Mission:**", "**Learn It (Review Content)**".
const HEADER_LINE_RE = /^\s*(?:\d+\.\s*)?(?:[-*+]\s+)?\*\*(.+?)\*\*\s*:?\s*$/;

function cleanTitle(inner) {
  return String(inner || '').replace(/:\s*$/, '').trim();
}

function isDluTitle(title) {
  return DLU_TITLE_RES.some((re) => re.test(title));
}

/** True when a single line is a DLU part header (bold, canonical name). */
function isDluHeaderLine(line) {
  const m = HEADER_LINE_RE.exec(line || '');
  return !!m && isDluTitle(cleanTitle(m[1]));
}

function dluPartMarks(lines) {
  const marks = [];
  lines.forEach((line, i) => {
    const m = HEADER_LINE_RE.exec(line || '');
    if (!m) return;
    const title = cleanTitle(m[1]);
    if (isDluTitle(title)) marks.push({ i, title });
  });
  return marks;
}

/** True when the content is a DLU (day-based) blueprint. */
export function detectDluBlueprint(fullContent) {
  const text = fullContent || '';
  if (!text.trim()) return false;
  // Unambiguous marker emitted by the DLU blueprint prompt.
  if (/^\s*#{2,3}\s+DLU\s+Outline\b/im.test(text)) return true;
  // Otherwise require at least two canonical DLU part headers — one stray bold
  // label in a module blueprint is not enough to reshape it.
  return dluPartMarks(text.split('\n')).length >= 2;
}

/**
 * Split a DLU day blueprint into UI sections: an "Overview" (the header before the
 * first DLU part) followed by one section per DLU part. Each part's content keeps
 * its own header line verbatim so a split/rebuild round trip preserves formatting.
 * @returns {{ title: string, content: string }[]}
 */
export function parseDluBlueprintSections(fullContent) {
  const text = fullContent || '';
  const lines = text.split('\n');
  const marks = dluPartMarks(lines);
  if (marks.length === 0) return [];

  const out = [];
  const overview = lines.slice(0, marks[0].i).join('\n').trim();
  if (overview) out.push({ title: 'Overview', content: overview });

  marks.forEach((mk, idx) => {
    const end = idx + 1 < marks.length ? marks[idx + 1].i : lines.length;
    const content = lines.slice(mk.i, end).join('\n').trim();
    out.push({ title: mk.title, content });
  });
  return out;
}

function reconstruct(sections) {
  return sections
    .map((s) => (s.content || '').trim())
    .filter(Boolean)
    .join('\n\n');
}

/**
 * Replace one DLU section (by title, or 'Overview') and return the rebuilt
 * blueprint, preserving the structure of the other parts. If a regenerated part
 * comes back body-only (no bold header line) its original header line is
 * re-prepended, so the part's heading/number is never lost.
 */
export function replaceDluBlueprintSection(fullContent, title, newContent) {
  const sections = parseDluBlueprintSections(fullContent);
  if (sections.length === 0) return fullContent;

  const next = sections.map((s) => {
    if (s.title !== title) return s;
    let content = (newContent || '').trim();
    if (s.title !== 'Overview') {
      const firstNew = content.split('\n')[0] || '';
      const firstOrig = (s.content.split('\n')[0] || '').trim();
      if (!isDluHeaderLine(firstNew) && isDluHeaderLine(firstOrig)) {
        content = content ? `${firstOrig}\n${content}` : firstOrig;
      }
    }
    return { ...s, content };
  });
  return reconstruct(next);
}
