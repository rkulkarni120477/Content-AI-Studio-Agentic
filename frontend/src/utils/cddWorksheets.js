/**
 * DLU CDD worksheet parsing & splicing.
 *
 * A DLU (day-based "Block") CDD stores all of its content under the single
 * "Course Structure" section, internally organised as a title/metadata header
 * followed by several worksheet blocks. This util splits that blob into an
 * Overview block + one block per worksheet (for tabbed rendering) and can
 * splice an edited/regenerated worksheet back into the full blob so the
 * existing single-section save/patch path can commit it unchanged.
 *
 * Standard (module/lesson) CDDs have no worksheet labels, so
 * detectDluCddContent returns false and none of this applies.
 *
 * Boundary detection is deliberately decoration-blind. The output format comes
 * from whichever prompt generated the CDD, not from CAS, and prompts get
 * rewritten — the same prompt has emitted `## WORKSHEET 1: X` one day and
 * `**Worksheet 1: X**` the next. Matching only markdown headings silently
 * demoted those CDDs to a flat blob, so we strip decoration and match the label.
 *
 * Mirrored in promptops_app/parsers/cdd_parser.py — the two must agree or the
 * screen and the XLSX export disagree about where worksheets start. Shared
 * fixtures live in this util's test file and tests/characterization/
 * test_dlu_cdd_export.py.
 */
import { parseCddFlat } from '@utils/cddContent';

// Leading noise: blockquote markers, list bullets, markdown heading hashes.
// Group 1 captures the hashes so a real heading can be told from a bare label.
const WS_LEAD_RE = /^[\s>]*(?:[-*+]\s+)?(?:(#{1,6})\s*)?/;
const WS_LABEL_RE = /^(?:worksheet|work\s*sheet|sheet|ws)\s*#?\s*(\d{1,2})\b\s*[:\-–—.]?\s*(.*)$/i;
// A boundary label is a short line. Anything longer is prose that merely
// mentions a worksheet ("...as recorded in Worksheet 2 of the prior block").
const WS_MAX_LABEL_LEN = 120;

/**
 * Parse a worksheet boundary label out of one line, ignoring decoration.
 * Accepts `## WORKSHEET 1: X`, `### Worksheet 1: X`, `**Worksheet 1: X**`,
 * `Worksheet 1 — X`, `- **Sheet 1: X**`. Returns null for anything else.
 */
function worksheetMark(line) {
  const lead = WS_LEAD_RE.exec(line || '');
  const isHeading = Boolean(lead && lead[1]);
  let text = (line || '').slice(lead ? lead[0].length : 0).trim();
  // Emphasis wrappers, then a trailing colon left behind by "**Worksheet 1:**".
  text = text.replace(/^(\*{1,2}|_{1,2})/, '');
  text = text.replace(/(\*{1,2}|_{1,2})\s*:?\s*$/, '').trim();
  text = text.replace(/:+$/, '').trim();
  if (!text || text.length > WS_MAX_LABEL_LEN) return null;
  const m = WS_LABEL_RE.exec(text);
  if (!m) return null;
  return {
    num: parseInt(m[1], 10),
    title: (m[2] || '').trim(),
    label: text,
    isHeading,
  };
}

/**
 * Keep one mark per worksheet number — whichever has the most content.
 * A table of contents printed before the real worksheets would otherwise split
 * the document at the TOC entries and emit a run of near-empty worksheets.
 */
function dedupeWorksheetMarks(marks, textLen) {
  const best = new Map();
  marks.forEach((mark, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].start : textLen;
    const body = end - mark.start;
    const current = best.get(mark.num);
    if (!current || body > current.body) best.set(mark.num, { body, mark });
  });
  return [...best.values()].map((e) => e.mark).sort((a, b) => a.start - b.start);
}

/** All worksheet boundary marks in `text`, with their line start offsets. */
function findWorksheetMarks(text) {
  const marks = [];
  let pos = 0;
  (text || '').split('\n').forEach((line) => {
    const mark = worksheetMark(line);
    if (mark) marks.push({ ...mark, start: pos });
    pos += line.length + 1; // +1 for the consumed newline
  });
  return dedupeWorksheetMarks(marks, (text || '').length);
}

/** Worksheet marks when `text` is a DLU CDD, else []. */
function dluWorksheetMarks(text) {
  const marks = findWorksheetMarks(text);
  if (marks.length === 0) return [];
  // A markdown-heading marker is unambiguous on its own. This is the original
  // rule, kept intact so nothing that renders as DLU today can stop doing so.
  if (marks.some((m) => m.isHeading)) return marks;
  // Bold/plain labels are weaker evidence, so require two distinct worksheet
  // numbers before reshaping the document — one stray mention is not a DLU CDD.
  if (new Set(marks.map((m) => m.num)).size >= 2) return marks;
  return [];
}

/** True when the CDD content is worksheet-based (DLU). */
export function detectDluCddContent(fullContent) {
  if (!fullContent) return false;
  return dluWorksheetMarks(fullContent).length > 0;
}

/** Return the "Course Structure" text that holds the worksheets. */
export function getCourseStructureText(fullContent, sections) {
  if (sections && typeof sections === 'object' && sections['Course Structure']) {
    return sections['Course Structure'];
  }
  const parsed = parseCddFlat(fullContent || '');
  return parsed['Course Structure'] || fullContent || '';
}

const _ACRONYMS = new Set(['ACS', 'DLU', 'FAA', 'PPE', 'SDS', 'BOM', 'IPC', 'CDD']);

function _titleCaseShort(s) {
  return (s || '')
    .trim()
    .split(/\s+/)
    .map((w) => {
      const bare = w.replace(/[^A-Za-z]/g, '');
      if (_ACRONYMS.has(bare.toUpperCase())) return w.toUpperCase();
      return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
    })
    .join(' ');
}

/**
 * Split the Course Structure blob into an overview + worksheet blocks.
 * @returns {{ overview: string, worksheets: {key,num,title,label,content}[] }}
 */
export function parseCddWorksheets(csText) {
  const text = csText || '';
  const marks = dluWorksheetMarks(text);

  if (marks.length === 0) {
    return { overview: text.trim(), worksheets: [] };
  }

  const overview = text.slice(0, marks[0].start).trim();
  const worksheets = marks.map((mk, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].start : text.length;
    // Content keeps the model's original label line verbatim, decoration and
    // all, so a split/rebuild round trip preserves the source formatting.
    const content = text.slice(mk.start, end).trim();
    const label = mk.title ? `${mk.num}. ${_titleCaseShort(mk.title)}` : `Worksheet ${mk.num}`;
    // `title` is the decoration-stripped label line ("WORKSHEET 1: BLOCK
    // OVERVIEW") — it is sent as the section key when regenerating.
    return { key: `worksheet_${mk.num}`, num: mk.num, title: mk.label, label, content };
  });

  return { overview, worksheets };
}

function _reconstruct(overview, worksheets) {
  const parts = [];
  if ((overview || '').trim()) parts.push(overview.trim());
  worksheets.forEach((w) => {
    if ((w.content || '').trim()) parts.push(w.content.trim());
  });
  return parts.join('\n\n');
}

/**
 * Replace one worksheet (or the 'overview' block) inside the Course Structure
 * text and return the reconstructed blob.
 */
export function replaceWorksheet(csText, key, newContent) {
  const { overview, worksheets } = parseCddWorksheets(csText);
  if (key === 'overview') {
    return _reconstruct(newContent, worksheets);
  }
  const next = worksheets.map((w) => {
    if (w.key !== key) return w;
    let content = (newContent || '').trim();
    // Preserve the worksheet's own boundary label line. The section-regenerate
    // prompt returns body-only content (no label); without re-prepending it the
    // boundary marker is lost and worksheets merge on the next split/export.
    // Manual edits already carry the label, so this is a no-op there.
    if (!worksheetMark(content.split('\n')[0] || '')) {
      const firstLine = (w.content.split('\n')[0] || '').trim();
      const origHeading = worksheetMark(firstLine) ? firstLine : '';
      if (origHeading) content = content ? `${origHeading}\n\n${content}` : origHeading;
    }
    return { ...w, content };
  });
  return _reconstruct(overview, next);
}

/**
 * Build the tab blocks for a DLU CDD: an Overview block first (if present),
 * then one per worksheet. Each block carries `dluKey`/`dluTitle` so the editor
 * can splice edits/regens back into Course Structure.
 */
export function buildDluWorksheetBlocks(fullContent, sections) {
  const csText = getCourseStructureText(fullContent, sections);
  const { overview, worksheets } = parseCddWorksheets(csText);
  const blocks = [];
  if ((overview || '').trim()) {
    blocks.push({
      key: 'overview',
      label: '🗂️ Overview',
      content: overview.trim(),
      dluKey: 'overview',
      dluTitle: 'Overview',
    });
  }
  worksheets.forEach((w) => {
    blocks.push({
      key: w.key,
      label: `🗂️ ${w.label}`,
      content: w.content,
      dluKey: w.key,
      dluTitle: w.title,
    });
  });
  return { csText, blocks };
}
