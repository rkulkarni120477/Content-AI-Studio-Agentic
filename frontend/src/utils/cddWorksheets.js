/**
 * DLU CDD worksheet parsing & splicing.
 *
 * A DLU (day-based "Block") CDD stores all of its content under the single
 * "Course Structure" section, internally organised as a title/metadata header
 * followed by several `## WORKSHEET N: TITLE` blocks. This util splits that blob
 * into an Overview block + one block per worksheet (for tabbed rendering) and
 * can splice an edited/regenerated worksheet back into the full blob so the
 * existing single-section save/patch path can commit it unchanged.
 *
 * Standard (module/lesson) CDDs have no `## WORKSHEET` headings, so
 * detectDluCddContent returns false and none of this applies.
 */
import { parseCddFlat } from '@utils/cddContent';

const WORKSHEET_HEADING_RE = /(^|\n)(#{1,3}\s*WORKSHEET\s+(\d+)\b[^\n]*)/gi;

/** True when the CDD content is worksheet-based (DLU). */
export function detectDluCddContent(fullContent) {
  if (!fullContent) return false;
  return /(^|\n)#{1,3}\s*WORKSHEET\s+\d+\b/i.test(fullContent);
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
  const marks = [];
  let m;
  const re = new RegExp(WORKSHEET_HEADING_RE.source, WORKSHEET_HEADING_RE.flags);
  while ((m = re.exec(text)) !== null) {
    const headingStart = m.index + m[1].length; // skip the leading newline
    marks.push({ num: parseInt(m[3], 10), heading: m[2].trim(), start: headingStart });
  }

  if (marks.length === 0) {
    return { overview: text.trim(), worksheets: [] };
  }

  const overview = text.slice(0, marks[0].start).trim();
  const worksheets = marks.map((mk, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].start : text.length;
    const content = text.slice(mk.start, end).trim();
    const fullTitle = mk.heading.replace(/^#{1,3}\s*/, '').trim(); // "WORKSHEET 1: BLOCK OVERVIEW"
    const shortName = fullTitle
      .replace(/^WORKSHEET\s+\d+\s*[:\-–—.]?\s*/i, '')
      .trim();
    const label = shortName ? `${mk.num}. ${_titleCaseShort(shortName)}` : `Worksheet ${mk.num}`;
    return { key: `worksheet_${mk.num}`, num: mk.num, title: fullTitle, label, content };
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
  const next = worksheets.map((w) => (w.key === key ? { ...w, content: newContent } : w));
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
