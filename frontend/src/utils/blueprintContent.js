/**
 * Blueprint section parsing & UI filtering (Streamlit render_blueprint_content parity).
 */
import {
  detectDluBlueprint,
  firstDluPartLine,
  parseDluBlueprintSections,
} from './dluBlueprint';

const BP_UI_HIDDEN_SECTION_KEYS = [
  'step 1 module identification',
  'step 5 blueprint validation',
  'blueprint validation',
  'narrative and modularity',
  'narrative',
  'modularity',
  'blueprint complete',
  'blueprint ready',
  'blueprint is complete',
  'blueprint is ready',
];

const UI_STRIP_PATTERNS = [
  /^(?:[•\-*])?\s*\*{0,2}validation\s+complete\.?\*{0,2}\s*$/gim,
  /^(?:[•\-*])?\s*✅\s*validation\s+complete\.?\s*$/gim,
  /^\s*\*{0,2}✅\s*validation\s+complete\*{0,2}.*$/gim,
  /^.*\bvalidation\s+complete\b.*$/gim,
  /^(?:[•\-*])?\s*\*{0,2}blueprint\s+(is\s+)?(complete|ready|validated)\.?\*{0,2}\s*$/gim,
  /^(?:[•\-*])?\s*✅\s*blueprint\s+(is\s+)?(complete|ready|validated)\.?\s*$/gim,
  /^.*\bblueprint\s+(is\s+)?(complete|ready|validated)\b.*$/gim,
  /^.*blueprint\s+is\s+ready\s+for\s+(lesson|content)\s+generation.*$/gim,
  /^#+\s*step\s+[56]\s*[:\-—]?\s*(blueprint\s+)?validation.*$/gim,
  /^#+\s*(blueprint\s+)?validation\s*(check|complete|summary|confirmed)?.*$/gim,
];

export function isBlueprintSectionHidden(title) {
  const tl = (title || '').trim().toLowerCase();
  for (const key of BP_UI_HIDDEN_SECTION_KEYS) {
    if (tl.includes(key)) return true;
  }
  if (/^step\s+[15]\b/.test(tl)) return true;
  if (/blueprint\s+(complete|ready|validated|done)/.test(tl)) return true;
  if (/blueprint\s+output/.test(tl)) return true;
  return false;
}

export function stripUiHiddenText(text) {
  let result = text || '';
  UI_STRIP_PATTERNS.forEach((pat) => {
    result = result.replace(pat, '');
  });
  return result.replace(/\n{3,}/g, '\n\n').trim();
}

export function parseSectionsFromText(text) {
  const sections = {};
  let currentTitle = null;
  let currentLines = [];

  (text || '').split('\n').forEach((line) => {
    if (line.startsWith('## ')) {
      if (currentTitle !== null) {
        const body = currentLines.join('\n').trim();
        if (body) sections[currentTitle] = body;
      }
      currentTitle = line.replace(/^##\s+/, '').trim();
      currentLines = [];
    } else if (currentTitle !== null) {
      currentLines.push(line);
    }
  });

  if (currentTitle !== null) {
    const body = currentLines.join('\n').trim();
    if (body) sections[currentTitle] = body;
  }

  const cleaned = {};
  Object.entries(sections).forEach(([title, content]) => {
    const newTitle = ['Purpose', 'Module Purpose'].includes(title.trim())
      ? title.replace('Purpose', 'Goal')
      : title;
    cleaned[newTitle] = content.replace(/Purpose:/g, 'Goal:');
  });
  return cleaned;
}

export function normalizeBlueprintSectionTitle(title) {
  let display = (title || '').trim();
  display = display.replace(/^step\s+\d+\s*[:\-–—.]\s*/i, '').trim();
  display = display.replace(/lesson\s+structure\s*\(planks?\)/gi, 'Lesson Structure (Topics)');
  if (/^lesson structure$/i.test(display)) {
    display = 'Lesson Structure (Topics)';
  }
  return display.replace(/Purpose/g, 'Goal');
}

/**
 * Title of the synthetic section that stands in for a document whose structure
 * no parser recognised. Sections carrying `whole: true` are the WHOLE document,
 * so an edit to one replaces the document rather than being spliced into it.
 */
export const WHOLE_DOCUMENT_SECTION_TITLE = 'Full Document';

/**
 * True when a `## ` section starts after `lineIdx`.
 *
 * The DLU parser assigns every line after the last part header to that part, so
 * reshaping a document into DLU parts is only safe when nothing structural
 * follows them. A blueprint that nests "### 1. TODAY'S MISSION … ### 5. DAY
 * REFLECTION" under "## SECTION OUTLINES" and then continues with "## OPEN
 * ITEMS" / "## SUMMARY" is a `## `-sectioned document, not a DLU-shaped one:
 * treating it as DLU buries those trailing sections inside Day Reflection,
 * where regenerating Day Reflection deletes them. Real shape — blueprints 223
 * and 224. The canonical DLU outline puts its own `## ` blocks ABOVE the parts
 * for this reason, so it passes.
 */
function hasSectionHeadingAfter(fullContent, lineIdx) {
  if (lineIdx < 0) return false;
  return (fullContent || '')
    .split('\n')
    .slice(lineIdx + 1)
    // Same predicate parseSectionsFromText uses, so the two agree on what a
    // section heading is ("### " has three hashes and does not match).
    .some((line) => line.startsWith('## '));
}

/**
 * @returns {{ title: string, content: string, dlu?: boolean, whole?: boolean }[]}
 *
 * `dlu: true` marks a DLU part — an edit to one is spliced back by
 * `replaceDluBlueprintSection`. `whole: true` marks the whole document. Callers
 * must carry these flags into their save/regenerate path rather than re-deriving
 * the shape, or the splice can disagree with what was displayed.
 *
 * Never returns [] for a document that has content: when neither the DLU parser
 * nor the `## ` parser finds anything, the document is returned as one
 * whole-document section. The page renders its editing controls per section, so
 * an empty list used to mean the reader lost Save, Regenerate and per-item
 * regeneration with nothing on screen saying why — a silent, shape-dependent
 * loss of every control on the page.
 */
export function buildBlueprintUiSections(fullContent, sectionsObj) {
  // DLU (day-based) blueprints aren't organised as `## ` sections — they use a
  // "### DLU Outline" + numbered-part shape. Parse that so the page shows the
  // same per-section Save/Regenerate controls. Titles/content are kept verbatim
  // (no normalize/strip) so the splice-back in BlueprintPage matches exactly.
  // Standard blueprints have no DLU markers -> detectDluBlueprint false -> the
  // existing `## ` logic below runs unchanged.
  if (detectDluBlueprint(fullContent)
      && !hasSectionHeadingAfter(fullContent, firstDluPartLine(fullContent))) {
    const dlu = parseDluBlueprintSections(fullContent)
      .map((s) => ({ title: s.title, content: (s.content || '').trim(), dlu: true }))
      .filter((s) => s.content);
    if (dlu.length) return dlu;
  }

  let raw = {};
  if (sectionsObj && typeof sectionsObj === 'object' && Object.keys(sectionsObj).length > 0) {
    raw = sectionsObj;
  } else if (fullContent) {
    raw = parseSectionsFromText(fullContent);
  }

  const items = [];
  Object.entries(raw).forEach(([title, content]) => {
    if (isBlueprintSectionHidden(title)) return;
    const stripped = stripUiHiddenText(content || '');
    if (!stripped) return;
    items.push({
      title: normalizeBlueprintSectionTitle(title),
      content: stripped,
    });
  });
  if (items.length === 0) {
    // Verbatim, not stripped: this content is committed back as the whole
    // document, so removing the UI-hidden validation lines here would delete
    // them from the stored blueprint on the first save.
    const whole = (fullContent || '').trim();
    if (whole) return [{ title: WHOLE_DOCUMENT_SECTION_TITLE, content: whole, whole: true }];
  }
  return items;
}
