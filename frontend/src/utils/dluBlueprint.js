/**
 * DLU (day-based) Block Blueprint parsing & splicing.
 *
 * A DLU day blueprint is NOT organised as `## ` sections like a module blueprint.
 * It is a short metadata header (Day Number / Day Type / Topic / Codes) followed
 * by the DLU parts. The DLU generator has emitted those parts in several shapes:
 *
 *   • numbered bold under a heading:   "### DLU Outline" then "1. **Today's Mission**"
 *   • non-numbered bold under heading: "### DLU Outline" then "**Today's Mission**"
 *   • bold-with-colon, no heading:     "**Today's Mission:**"
 *   • bold label with its body inline: "**Today's Mission:** Understanding …"
 *   • markdown headings:               "#### Today's Mission"
 *   • plain numbered lines:            "1. Today's Mission"
 *
 * The Blueprint page's standard `## `-section parser finds nothing in any of
 * these, so no per-section Save/Regenerate controls appear. These helpers treat
 * each DLU part as a section (plus an "Overview" for the header) and splice an
 * edited/regenerated part back in without disturbing the others.
 *
 * The live DLU prompt pins no markdown shape (its OUTPUT FORMAT asks for "a
 * structured outline", nothing more), so which of the shapes above comes back is
 * decided per generation by the model. Recognising only the first three is why
 * the controls silently vanished on some days' outlines and not others.
 *
 * Detection is gated to the canonical DLU part NAMES, so it never mistakes an
 * ordinary bold label (e.g. "**Topic Label:**") for a part, and a standard module
 * blueprint (which has none of these names as bold headers, and uses `## `
 * sections) returns false — this is purely additive to the DLU path.
 */

// The canonical DLU parts, in order. Used by the strict pass, which requires the
// line to be nothing BUT the part name, so a prefix match is safe there:
// "Learn It (Review Content)" is the review-day spelling of "Learn It".
const DLU_TITLE_RES = [
  /^today'?s mission\b/i,
  /^learn\s*it\b/i,
  /^quick check\b/i,
  /^up next\b/i,          // "Up Next" / "Up Next in Class"
  /^(?:day reflection|reflection)\b/i,
];

// The same names, but matched WHOLE. The relaxed passes below accept lines that
// carry body text after the name, where a prefix match would misfire: a Quick
// Check body legitimately opens with "- **Quick Check Targeting:** …", which
// /^quick check\b/ matches and would turn into a spurious part boundary.
const DLU_TITLE_EXACT_RES = [
  /^today'?s\s+mission$/i,
  /^learn\s*it(?:\s*\([^)]*\))?$/i,
  /^quick\s+check$/i,
  /^up\s+next(?:\s+in\s+class)?$/i,
  /^(?:day\s+)?reflections?$/i,
];

function cleanTitle(inner) {
  return String(inner || '')
    .trim()
    .replace(/^\*+|\*+$/g, '')
    .trim()
    .replace(/:\s*$/, '')
    .trim();
}

function isDluTitle(title) {
  return DLU_TITLE_RES.some((re) => re.test(title));
}

function isExactDluTitle(title) {
  return DLU_TITLE_EXACT_RES.some((re) => re.test(title));
}

/**
 * A pass returns `{ title, label }` for a part-header line, or null.
 *
 * `label` is the part of the line that names the part — the whole line for the
 * shapes that carry nothing else, and just the leading label for the inline
 * shape. It is what gets re-prepended when a regenerated part comes back
 * body-only, so it must never include the part's own body text.
 */

/** "1. **Today's Mission**" / "**Today's Mission**" / "**Today's Mission:**" */
function strictPass(line) {
  const m = /^\s*(?:\d+[.)]\s*)?(?:[-*+]\s+)?\*\*(.+?)\*\*\s*:?\s*$/.exec(line || '');
  if (!m) return null;
  const title = cleanTitle(m[1]);
  return isDluTitle(title) ? { title, label: String(line).trim() } : null;
}

/** "#### Today's Mission" / "### 3. **Quick Check**" */
function headingPass(line) {
  const m = /^\s*#{1,6}\s*(?:\d+[.)]\s*)?(.+?)\s*:?\s*$/.exec(line || '');
  if (!m) return null;
  const title = cleanTitle(m[1]);
  return isExactDluTitle(title) ? { title, label: String(line).trim() } : null;
}

/** "**Today's Mission:** Understanding exploded views …" (body on the same line) */
function inlineBoldPass(line) {
  const m = /^(\s*(?:\d+[.)]\s*)?(?:[-*+]\s+)?\*\*(.+?)\*\*\s*:?)/.exec(line || '');
  if (!m) return null;
  const title = cleanTitle(m[2]);
  return isExactDluTitle(title) ? { title, label: m[1].trim() } : null;
}

/** "1. Today's Mission" (no bold at all) */
function plainNumberedPass(line) {
  const m = /^\s*\d+[.)]\s*(.+?)\s*:?\s*$/.exec(line || '');
  if (!m) return null;
  const title = cleanTitle(m[1]);
  return isExactDluTitle(title) ? { title, label: String(line).trim() } : null;
}

// Ordered strictest-first. Only the first pass that finds a real set of parts is
// used, so a document the strict pass already understands is parsed exactly as
// it was before the relaxed passes existed — they can only rescue a document
// that would otherwise have yielded no sections and therefore no controls.
const PART_PASSES = [strictPass, headingPass, inlineBoldPass, plainNumberedPass];

/** The first pass matching this line, or null. Used to test a single line. */
function dluHeaderMark(line) {
  for (const pass of PART_PASSES) {
    const mark = pass(line);
    if (mark) return mark;
  }
  return null;
}

/** True when a single line is a DLU part header in any recognised shape. */
function isDluHeaderLine(line) {
  return dluHeaderMark(line) !== null;
}

/**
 * Part-header positions in a document: `{ i, title, label }[]`, in line order.
 *
 * Two parts is the threshold for "this pass understood the document" — one
 * canonical name on its own is as likely to be prose as a real boundary. When no
 * pass clears it, the strict pass's own result is returned so single-part
 * behaviour is unchanged.
 */
function dluPartMarks(lines) {
  let strictMarks = null;
  for (const pass of PART_PASSES) {
    const marks = [];
    lines.forEach((line, i) => {
      const mark = pass(line);
      if (mark) marks.push({ i, ...mark });
    });
    if (strictMarks === null) strictMarks = marks;
    if (marks.length >= 2) return marks;
  }
  return strictMarks || [];
}

/**
 * Line index of the first DLU part header, or -1.
 *
 * Callers need this to tell whether reshaping the document into DLU parts would
 * swallow structure that follows them: every line after the last part header is
 * assigned to that part, so a trailing appendix becomes part of Day Reflection
 * and regenerating Day Reflection would delete it.
 */
export function firstDluPartLine(fullContent) {
  const marks = dluPartMarks((fullContent || '').split('\n'));
  return marks.length ? marks[0].i : -1;
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
 * comes back body-only (no part-header line) its original label is re-prepended,
 * so the part's heading/number is never lost.
 *
 * Returns the document unchanged when it holds no recognised parts — the caller
 * is responsible for not routing an edit here in that case (the Blueprint page
 * commits such a document whole), because silently returning the original is
 * indistinguishable from a saved edit.
 */
export function replaceDluBlueprintSection(fullContent, title, newContent) {
  const sections = parseDluBlueprintSections(fullContent);
  if (sections.length === 0) return fullContent;

  const next = sections.map((s) => {
    if (s.title !== title) return s;
    let content = (newContent || '').trim();
    if (s.title !== 'Overview') {
      const firstNew = content.split('\n')[0] || '';
      const origMark = dluHeaderMark(s.content.split('\n')[0] || '');
      if (!isDluHeaderLine(firstNew) && origMark) {
        content = content ? `${origMark.label}\n${content}` : origMark.label;
      }
    }
    return { ...s, content };
  });
  return reconstruct(next);
}
