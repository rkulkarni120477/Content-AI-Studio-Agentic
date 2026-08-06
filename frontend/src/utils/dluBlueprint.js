/**
 * DLU (day-based) Block Blueprint parsing & splicing.
 *
 * A DLU day blueprint is NOT organised as `## ` sections like a module blueprint.
 * It is a short metadata header (Day Number / Day Type / Topic / Codes) followed
 * by `### DLU Outline` and a numbered list of bold-titled parts:
 *
 *   1. **Today's Mission**
 *      - ...
 *   2. **Learn It**
 *      - ...
 *   3. **Quick Check**  ... 4. **Up Next in Class** ... 5. **Day Reflection**
 *
 * The Blueprint page's standard `## `-section parser finds nothing in that shape,
 * so no per-section Save/Regenerate controls appear. These helpers let the page
 * treat each numbered DLU part as a section (plus an "Overview" for the header),
 * and splice an edited/regenerated part back into the day blueprint without
 * disturbing the numbering or the other parts.
 *
 * Standard module blueprints have none of these markers, so detectDluBlueprint
 * returns false and the existing `## ` logic is used unchanged — this is purely
 * additive to the DLU path.
 */

// "1. **Today's Mission**" (number + bold title). End is not anchored so a title
// with trailing text still matches; the whole line is kept verbatim in the block.
const NUM_ITEM_RE = /^\s*\d+\.\s+\*\*(.+?)\*\*/;

// The canonical DLU part names — used only as corroborating evidence when the
// "### DLU Outline" marker is absent, so we never misread a module blueprint.
const DLU_NAME_RE = /today'?s mission|learn\s*it|quick check|up next|day reflection|reflection/i;

function numberedItemMarks(lines) {
  const marks = [];
  lines.forEach((line, i) => {
    const m = NUM_ITEM_RE.exec(line || '');
    if (m) marks.push({ i, title: m[1].trim() });
  });
  return marks;
}

/** True when the content is a DLU (day-based) blueprint. */
export function detectDluBlueprint(fullContent) {
  const text = fullContent || '';
  if (!text.trim()) return false;
  // Unambiguous marker emitted by the DLU blueprint prompt.
  if (/^\s*#{2,3}\s+DLU\s+Outline\b/im.test(text)) return true;
  // Otherwise require at least two DLU-named numbered parts — one stray numbered
  // item in a module blueprint is not enough to reshape it.
  const dlu = numberedItemMarks(text.split('\n')).filter((mk) => DLU_NAME_RE.test(mk.title));
  return dlu.length >= 2;
}

/**
 * Split a DLU day blueprint into UI sections: an "Overview" (the header before the
 * first numbered part) followed by one section per numbered DLU part. Each part's
 * content keeps its own "N. **Title**" line verbatim so a split/rebuild round trip
 * preserves the numbering.
 * @returns {{ title: string, content: string }[]}
 */
export function parseDluBlueprintSections(fullContent) {
  const text = fullContent || '';
  const lines = text.split('\n');
  const marks = numberedItemMarks(lines);
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
 * blueprint, preserving the numbered structure of the other parts. If a
 * regenerated part comes back body-only (no "N. **Title**" line) its original
 * label line is re-prepended, so the numbering/heading is never lost.
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
      if (!NUM_ITEM_RE.test(firstNew) && NUM_ITEM_RE.test(firstOrig)) {
        content = content ? `${firstOrig}\n${content}` : firstOrig;
      }
    }
    return { ...s, content };
  });
  return reconstruct(next);
}
