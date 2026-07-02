/**
 * Client-side CDD parsing for Blueprint module / course-end selectors (Streamlit parity).
 */

const MARKER_RE = /(?:^|\n)\s*#*\s*(Course Details|Course Structure|Course[\s-]+Level Assessment|Validation)\s*:?\s*\n/gi;

export function parseCddFlat(rawText) {
  const result = {
    'Course Details': '',
    'Course Structure': '',
    'Course Level Assessment': '',
  };
  const text = (rawText || '').trim();
  if (!text) return result;

  const positions = [];
  let m;
  const re = new RegExp(MARKER_RE.source, MARKER_RE.flags);
  while ((m = re.exec(text)) !== null) {
    const label = m[1].trim().toLowerCase().replace(/[\s-]+/g, ' ');
    let key = null;
    if (label === 'course details') key = 'Course Details';
    else if (label === 'course structure') key = 'Course Structure';
    else if (label === 'course level assessment') key = 'Course Level Assessment';
    if (key) positions.push({ start: m.index, end: m.index + m[0].length, key });
  }

  if (positions.length === 0) {
    result['Course Structure'] = text;
    return result;
  }

  positions.forEach((pos, i) => {
    const nextStart = i + 1 < positions.length ? positions[i + 1].start : text.length;
    result[pos.key] = text.slice(pos.end, nextStart).trim();
  });
  return result;
}

// Tolerates the legacy "Module no.: 1" form some older CDDs produced, and an
// optional markdown heading prefix ("### Module 1"), in addition to the
// standard "Module 1" form.
const MODULE_NUM_RE = '#*\\s*[Mm]odule\\s*(?:[Nn]o\\.?\\s*:?\\s*)?(\\d+)';

function extractModuleCountFromStructure(structure) {
  const modRe = new RegExp(`(?:^|\\n)\\s*${MODULE_NUM_RE}`, 'g');
  let max = 0;
  let match;
  while ((match = modRe.exec(structure || '')) !== null) {
    max = Math.max(max, parseInt(match[1], 10));
  }
  return max || 0;
}

/**
 * @returns {{ label: string, key: number|string, isCourseEnd?: boolean }[]}
 */
export function buildModuleOptions(cddContent, existingModuleNumbers = new Set()) {
  const flat = parseCddFlat(cddContent);
  const structure = flat['Course Structure'] || '';
  const assessment = flat['Course Level Assessment'] || '';
  const options = [];

  const modTitleRe = new RegExp(`(?:^|\\n)\\s*${MODULE_NUM_RE}[:\\s\\-—]+([^\\n]+)`, 'g');
  const modMatches = [...structure.matchAll(modTitleRe)];
  if (modMatches.length > 0) {
    modMatches.forEach((mm) => {
      const num = parseInt(mm[1], 10);
      const title = mm[2].trim().replace(/\*+$/, '').trim();
      const done = existingModuleNumbers.has(num) ? ' ✓' : '';
      options.push({ label: `Module ${num}: ${title}${done}`, key: num });
    });
  } else {
    const n = extractModuleCountFromStructure(structure);
    const count = n > 0 ? n : 1;
    for (let mn = 1; mn <= count; mn += 1) {
      const done = existingModuleNumbers.has(mn) ? ' ✓' : '';
      options.push({ label: `Module ${mn}${done}`, key: mn });
    }
  }

  if (assessment.trim()) {
    const titlePat = /(?:^|\n)\s*(?:•\s*)?\*{0,2}[Tt]itle\*{0,2}\s*:?\s*\*{0,2}([^\n*]+?)\*{0,2}\s*(?:\n|$)/gm;
    let endTitles = [...assessment.matchAll(titlePat)]
      .map((x) => x[1].trim().replace(/[*:]+$/, '').trim())
      .filter(Boolean);

    if (endTitles.length === 0) {
      const optPat = /(?:^|\n)\s*Option\s+\d+\s*:?\s*\n?\s*(?:•\s*)?\*{0,2}[Tt]itle\*{0,2}\s*:?\s*\*{0,2}([^\n*]+?)\*{0,2}/gm;
      endTitles = [...assessment.matchAll(optPat)]
        .map((x) => x[1].trim().replace(/[*:]+$/, '').trim())
        .filter(Boolean);
    }

    endTitles.forEach((etitle, ei) => {
      options.push({ label: `📋 ${etitle}`, key: `end_${ei}`, isCourseEnd: true, courseEndLabel: etitle });
    });
  }

  return options;
}

export function existingModuleNumbersForCdd(blueprints, cddId) {
  const nums = new Set();
  (blueprints || []).forEach((bp) => {
    if (bp.cdd_id != null && bp.cdd_id !== cddId) return;
    const n = parseInt(bp.module_number, 10);
    if (!Number.isNaN(n)) nums.add(n);
  });
  return nums;
}

export function buildExtraInstructionsBlock({
  isCourseEnd,
  courseEndLabel,
  moduleNum,
  extraInstructions,
}) {
  const extra = (extraInstructions || '').trim();
  const extraPart = extra ? `\n\n**Additional Instructions:**\n${extra}` : '';

  if (isCourseEnd && courseEndLabel) {
    return (
      `**This is a Course-Level End item: '${courseEndLabel}'.**\n`
      + 'Generate the Blueprint specifically for this course-end item. '
      + 'It should align to all modules in the course.'
      + extraPart
    );
  }
  return (
    `**This is Module ${moduleNum} of the course.**\n`
    + `Generate the Blueprint specifically for Module ${moduleNum}. `
    + 'Lesson numbering should start from Lesson 1 within this module.'
    + extraPart
  );
}
