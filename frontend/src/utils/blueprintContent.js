/**
 * Blueprint section parsing & UI filtering (Streamlit render_blueprint_content parity).
 */
import { detectDluBlueprint, parseDluBlueprintSections } from './dluBlueprint';

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
 * @returns {{ title: string, content: string }[]}
 */
export function buildBlueprintUiSections(fullContent, sectionsObj) {
  // DLU (day-based) blueprints aren't organised as `## ` sections — they use a
  // "### DLU Outline" + numbered-part shape. Parse that so the page shows the
  // same per-section Save/Regenerate controls. Titles/content are kept verbatim
  // (no normalize/strip) so the splice-back in BlueprintPage matches exactly.
  // Standard blueprints have no DLU markers -> detectDluBlueprint false -> the
  // existing `## ` logic below runs unchanged.
  if (detectDluBlueprint(fullContent)) {
    const dlu = parseDluBlueprintSections(fullContent)
      .map((s) => ({ title: s.title, content: (s.content || '').trim() }))
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
  return items;
}
