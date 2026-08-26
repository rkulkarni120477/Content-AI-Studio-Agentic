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
 * Title of the synthetic section holding the text ABOVE the first heading of a
 * heading-sectioned document, so a document preamble stays editable instead of
 * being the one part of the page with no controls.
 */
export const DOCUMENT_HEADER_SECTION_TITLE = 'Overview';

/**
 * Heading levels the section parser falls back to, in preference order, when a
 * document yields no usable `## ` sections.
 *
 * `## ` is absent from the list because reaching the fallback means the `## `
 * parser already ran: either the document has no `## ` heading at all, or every
 * one it has was deliberately hidden (`isBlueprintSectionHidden`) — and
 * re-offering hidden sections through another door would defeat that.
 *
 * `#` comes first because a document written entirely in `# ` headings is
 * outlined by them; the two-heading floor below means a lone `# Day 9 Blueprint`
 * title line does not win and the real `### ` structure underneath does. Real
 * shape — blueprint_versions 394, a Day 9 outline that is 15 × `### ` and 0 × `## `.
 */
const HEADING_FALLBACK_LEVELS = [1, 3, 4];

/**
 * At least this many headings must survive filtering before a level is accepted.
 *
 * One heading is not an outline: its section would exclude the preamble while
 * looking like the whole document, which is strictly worse than the honest
 * whole-document fallback.
 */
const HEADING_FALLBACK_MIN = 2;

/**
 * The title of an ATX heading at exactly `level`, or null.
 *
 * Exact-level by construction: `#{3}\s` cannot match `#### Title`, because the
 * character after the third `#` is a `#` and not whitespace.
 */
function headingAtLevel(line, level) {
  const m = new RegExp(`^#{${level}}\\s+(\\S.*?)\\s*$`).exec(line || '');
  return m ? m[1].trim() : null;
}

/**
 * Split a document on headings at one exact level.
 *
 * Every section carries a `locator` — the line range it occupies plus the exact
 * text of its own heading and of the heading that ends it. Edits are spliced
 * back by that range (`replaceHeadingSection`), never by rebuilding the document
 * from its parts: the heading line, its level, the preamble, the section order
 * and every untouched section therefore stay byte-identical. The heading line is
 * NOT part of `content`, so a section's identity cannot be edited out from under
 * it in the textarea.
 *
 * @returns {{ headingCount: number, sections: object[] } | null}
 */
function parseHeadingSections(fullContent, level) {
  const lines = (fullContent || '').split('\n');
  const marks = [];
  lines.forEach((line, i) => {
    const title = headingAtLevel(line, level);
    if (title !== null) marks.push({ i, title, text: line });
  });
  if (!marks.length) return null;

  const sections = [];
  const preamble = lines.slice(0, marks[0].i).join('\n').trim();
  if (preamble) {
    sections.push({
      title: DOCUMENT_HEADER_SECTION_TITLE,
      rawTitle: DOCUMENT_HEADER_SECTION_TITLE,
      content: preamble,
      // Never a real heading title, so it cannot collide with one.
      key: `${level}:preamble`,
      locator: {
        level,
        startLine: -1,
        endLine: marks[0].i,
        headingText: null,
        endHeadingText: marks[0].text,
      },
    });
  }
  marks.forEach((mk, idx) => {
    const nextMark = marks[idx + 1];
    const endLine = nextMark ? nextMark.i : lines.length;
    sections.push({
      title: normalizeBlueprintSectionTitle(mk.title),
      rawTitle: mk.title,
      // Verbatim, not stripUiHiddenText-ed: the draft is spliced straight back,
      // so stripping here would delete the UI-hidden validation lines from the
      // stored document on the first save. It also keeps an unedited save
      // byte-identical to what was already stored.
      content: lines.slice(mk.i + 1, endLine).join('\n').trim(),
      // Identity is the line index, not the title: two `### Screen: Quick Check`
      // headings in one document are a real possibility from a generator, and
      // keying on the title would collapse them into one accordion (and one
      // React key) while the range-based splice handled them correctly.
      key: `${level}:${mk.i}`,
      locator: {
        level,
        startLine: mk.i,
        endLine,
        headingText: mk.text,
        endHeadingText: nextMark ? nextMark.text : null,
      },
    });
  });
  return { headingCount: marks.length, sections };
}

/**
 * Every section at one heading level, filtered for display, or [].
 *
 * Shared by the primary `## ` path and the `# `/`### `/`#### ` fallback so both
 * produce the same shape: a locator per section, spliced back by line range.
 */
export function buildHeadingSectionsAtLevel(fullContent, level) {
  const parsed = parseHeadingSections(fullContent, level);
  if (!parsed) return [];
  return parsed.sections
    .filter((s) => !isBlueprintSectionHidden(s.rawTitle) && s.content)
    .map(({ title, content, key, locator }) => ({
      title,
      content,
      key,
      locator,
      heading: true,
    }));
}

/**
 * Sections for a document with no usable `## ` sections, or [].
 *
 * The shallowest level that yields enough headings wins, so the sections follow
 * the document's own outline rather than its deepest subdivision.
 */
export function buildHeadingFallbackSections(fullContent) {
  for (const level of HEADING_FALLBACK_LEVELS) {
    const built = buildHeadingSectionsAtLevel(fullContent, level);
    const headings = built.filter((s) => s.locator.startLine >= 0).length;
    if (headings >= HEADING_FALLBACK_MIN) return built;
  }
  return [];
}

/**
 * Resolve a locator against the document as it stands now: `[startLine, endLine]`,
 * or null when the section can no longer be identified.
 *
 * The range is recorded when the page renders and used when the user saves, so
 * it can be stale. Rather than trust it, both ends are verified against the
 * heading text they were taken from; on a mismatch the section is re-found by
 * that exact text, and only an ambiguous or missing heading gives up. Giving up
 * matters: splicing a drifted range would overwrite the wrong lines and report
 * success.
 */
function resolveHeadingRange(lines, locator) {
  const { level, startLine, endLine, headingText, endHeadingText } = locator || {};
  if (typeof level !== 'number') return null;

  const startIntact = headingText === null
    ? startLine === -1
    : startLine >= 0 && startLine < lines.length && lines[startLine] === headingText;
  const endIntact = endHeadingText === null
    ? endLine === lines.length
    : endLine >= 0 && endLine < lines.length && lines[endLine] === endHeadingText;
  if (startIntact && endIntact && endLine >= startLine + 1) return [startLine, endLine];

  if (headingText !== null) {
    const hits = [];
    lines.forEach((line, i) => { if (line === headingText) hits.push(i); });
    if (hits.length !== 1) return null;
    const start = hits[0];
    let end = lines.length;
    for (let i = start + 1; i < lines.length; i += 1) {
      if (headingAtLevel(lines[i], level) !== null) { end = i; break; }
    }
    return [start, end];
  }
  for (let i = 0; i < lines.length; i += 1) {
    if (headingAtLevel(lines[i], level) !== null) return [-1, i];
  }
  return null;
}

/**
 * Replace one heading section's body and return the rebuilt document, or null
 * when the section can no longer be located (the caller must fail the save
 * rather than write a document spliced at a guessed offset).
 *
 * Only the section's own body lines change. An unchanged body returns the
 * document untouched, so saving a section nobody edited is a byte-level no-op.
 */
export function replaceHeadingSection(fullContent, locator, newContent) {
  const lines = (fullContent || '').split('\n');
  const range = resolveHeadingRange(lines, locator);
  if (!range) return null;

  const [start, end] = range;
  const bodyStart = start + 1;
  const next = (newContent || '').trim();
  if (next === lines.slice(bodyStart, end).join('\n').trim()) return fullContent;

  // Blank lines fence the body off from the headings around it, so the markdown
  // still parses when an edit ends in a list or a table. Neither fence is added
  // where there is no heading to separate from.
  const lead = bodyStart === 0 ? [] : [''];
  const tail = end >= lines.length ? [] : [''];
  const body = next ? [...lead, ...next.split('\n'), ...tail] : lead;
  return [...lines.slice(0, bodyStart), ...body, ...lines.slice(end)].join('\n');
}

/**
 * @returns {{ title, content, dlu?, heading?, whole?, key?, locator? }[]}
 *
 * Each section reports HOW it must be committed back, and callers must carry
 * that through their save/regenerate path rather than re-deriving the shape —
 * a splice that disagrees with what was displayed writes the wrong lines and
 * still reports success:
 *
 *   • `dlu: true`     — a DLU part; splice with `replaceDluBlueprintSection`.
 *   • `heading: true` — a heading section; splice with `replaceHeadingSection`
 *                       using the section's `locator`.
 *   • `whole: true`   — the whole document; commit it verbatim.
 *
 * Sections may also carry `key`, a stable identity that is unique even when two
 * sections share a title. Use it, not the title, for UI state.
 *
 * Never returns [] for a document that has content. The page renders its editing
 * controls per section, so an empty list used to mean the reader lost Save,
 * Regenerate and per-item regeneration with nothing on screen saying why — a
 * silent, shape-dependent loss of every control on the page.
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

  // `## `-sectioned documents are spliced by line range like every other heading
  // level, rather than rebuilt from a parts dict.
  //
  // The rebuild was this shape's one remaining special case, and it rewrote the
  // WHOLE document on every single-section save or regenerate: it emitted
  // `## <title>` + trimmed body for each dict entry and joined them, so anything
  // above the first heading was dropped, headings at other levels were flattened
  // to `## `, order became dict order rather than document order, blank sections
  // disappeared, and every body was re-trimmed. Editing one section therefore
  // reformatted the rest — and this is the shape the default
  // blueprint_generation prompt produces ("## Step 1", "## Step 2"), so it was
  // the common case, not an edge one.
  //
  // Sections come from fullContent rather than the stored dict because only the
  // text carries line positions, heading levels and document order. The dict is
  // still honoured below for a version that has one but no `## ` heading in its
  // body.
  const bySection = buildHeadingSectionsAtLevel(fullContent, 2);
  if (bySection.length) return bySection;

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
    // Still structured, just not with `## `: a document outlined by `# ` or
    // `### ` headings gets a section per heading, spliced back by line range.
    // Without this it fell all the way through to the whole-document fallback,
    // where a 15-heading day outline could only be regenerated in one piece.
    const byHeading = buildHeadingFallbackSections(fullContent);
    if (byHeading.length) return byHeading;

    // Verbatim, not stripped: this content is committed back as the whole
    // document, so removing the UI-hidden validation lines here would delete
    // them from the stored blueprint on the first save.
    const whole = (fullContent || '').trim();
    if (whole) return [{ title: WHOLE_DOCUMENT_SECTION_TITLE, content: whole, whole: true }];
  }
  return items;
}
