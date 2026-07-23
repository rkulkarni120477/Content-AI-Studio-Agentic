/**
 * CDD section parsing & UI filtering (Streamlit render_cdd_content parity).
 */

import { parseCddFlat as parseFlatFromModules } from '@utils/blueprintModules';
import { stripUiHiddenText } from '@utils/blueprintContent';

export const CDD_UI_BLOCKS = [
  { key: 'Course Details', label: '📋 Title Details' },
  { key: 'Course Structure', label: '🗂️ Title Structure & Module Assessments' },
  { key: 'Course Level Assessment', label: '🏆 Title Level Assessment' },
];

export function parseCddFlat(rawText) {
  return parseFlatFromModules(rawText);
}

/** Strip Progression Logic lines from Course Structure (Streamlit parity). */
export function stripProgressionLogic(content) {
  const lines = (content || '').split('\n');
  const clean = [];
  let skip = false;
  for (const line of lines) {
    if (/progression\s+logic/i.test(line)) {
      skip = true;
      continue;
    }
    if (skip && (/^\s*(#{1,4}|\*{2}|-)/.test(line) || line.trim() === '')) {
      if (line.trim() !== '') skip = false;
    }
    if (!skip) clean.push(line);
  }
  return clean.join('\n').trim();
}

export function prepareCddBlockContent(blockKey, content) {
  let text = content || '';
  if (blockKey === 'Course Structure') {
    text = stripProgressionLogic(text);
  }
  return stripUiHiddenText(text);
}

/**
 * @returns {{ key: string, label: string, content: string }[]}
 */
export function buildCddUiBlocks(fullContent, sectionsObj) {
  const parsed = { ...parseCddFlat(fullContent) };
  if (sectionsObj && typeof sectionsObj === 'object') {
    Object.entries(sectionsObj).forEach(([key, val]) => {
      if (val && CDD_UI_BLOCKS.some((b) => b.key === key)) {
        parsed[key] = val;
      }
    });
  }

  return CDD_UI_BLOCKS
    .map(({ key, label }) => {
      const content = prepareCddBlockContent(key, parsed[key]);
      if (!content) return null;
      return { key, label, content };
    })
    .filter(Boolean);
}

/** Reconstruct full CDD markdown from parsed blocks (preserves hidden validation). */
export function rebuildCddFullContent(parsed) {
  const parts = [];
  for (const key of ['Course Details', 'Course Structure', 'Course Level Assessment', '_validation']) {
    const val = (parsed[key] || '').trim();
    if (!val) continue;
    if (key === '_validation') {
      parts.push(`## Validation\n${val}`);
    } else {
      parts.push(`## ${key}\n${val}`);
    }
  }
  return parts.join('\n\n');
}

/** Merge an edited block back into parsed sections + full content. */
export function patchCddBlock(fullContent, sectionsObj, blockKey, newContent) {
  const parsed = { ...parseCddFlat(fullContent) };
  if (sectionsObj && typeof sectionsObj === 'object') {
    Object.assign(parsed, sectionsObj);
  }
  parsed[blockKey] = newContent;
  const sections = {};
  CDD_UI_BLOCKS.forEach(({ key }) => {
    if (parsed[key]) sections[key] = parsed[key];
  });
  return {
    full_content: rebuildCddFullContent(parsed),
    sections,
  };
}
