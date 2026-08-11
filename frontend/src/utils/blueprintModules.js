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

// ---------------------------------------------------------------------------
// DLU (Daily Learning Unit) day-based CDDs
//
// Some CDDs (e.g. aviation "Block" curricula) are organised by teaching DAYS
// rather than Modules. They carry a day-by-day schedule table whose header is
// "| Day | Day Title | ... |". When such a schedule is present, the Blueprint
// creation dropdown should list Day 1..N instead of Module 1..N. This is
// purely additive — standard CDDs (no day table) keep the module behaviour.
// ---------------------------------------------------------------------------

// Split a markdown table row into trimmed cells, dropping the outer pipes.
function splitTableRow(line) {
  let cells = line.split('|');
  if (cells.length && cells[0].trim() === '') cells = cells.slice(1);
  if (cells.length && cells[cells.length - 1].trim() === '') cells = cells.slice(0, -1);
  return cells.map((c) => c.trim());
}

function isSeparatorRow(cells) {
  return cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c));
}

/**
 * Parse a DLU day-by-day schedule out of a CDD.
 *
 * Primary source: a markdown table whose header contains "Day" and "Day Title"
 * (the Instructional Sequence Map). Each data row is `| N | Title | ... |`.
 * Fallback: a "Total Instructional Days: N" marker -> generic Day 1..N.
 *
 * @returns {{ day: number, title: string }[]} ordered, de-duplicated by day.
 */
export function parseDaySchedule(cddContent) {
  const text = (cddContent || '').toString();
  if (!text.trim()) return [];

  const lines = text.split('\n');
  const days = [];
  const seen = new Set();

  // Locate the day-schedule table header. Recognised shapes: a "Day Title"
  // column (older Instructional Sequence Map tables), or "Day" as the first
  // column with "Topic" as the second (the block-wide digest pipeline's
  // Day-by-Day Map — its header is exactly
  // ["Day", "Topic", "Handbook Reference", ...], see _DAY_TABLE_HEADER in
  // block_wide_service.py). Requiring "Topic" specifically, not just a bare
  // "Day" first column, matters: a plain "Day" first-column match alone false-
  // positives on an ordinary Module CDD's own "Suggested Pacing" table (e.g.
  // "| Day | Activity | Duration |"), which would wrongly hide Module
  // selection and show "Select Day" for a course that has no days at all.
  let headerIdx = -1;
  for (let i = 0; i < lines.length; i += 1) {
    const l = lines[i];
    if (!l.includes('|')) continue;
    if (/day\s*title/i.test(l) && /\bday\b/i.test(l)) {
      headerIdx = i;
      break;
    }
    const cells = splitTableRow(l);
    if (cells.length > 1 && /^day$/i.test(cells[0]) && /^topic$/i.test(cells[1])) {
      headerIdx = i;
      break;
    }
  }

  if (headerIdx !== -1) {
    for (let i = headerIdx + 1; i < lines.length; i += 1) {
      const raw = lines[i];
      if (!raw.trim().startsWith('|')) break; // table ended
      const cells = splitTableRow(raw);
      if (isSeparatorRow(cells)) continue;
      const numStr = (cells[0] || '').trim();
      if (!/^\d+$/.test(numStr)) continue;
      const day = parseInt(numStr, 10);
      if (seen.has(day)) continue;
      let title = (cells[1] || '').trim().replace(/^\*+|\*+$/g, '').trim();
      title = title.replace(/<br\s*\/?>/gi, ' ').trim();
      seen.add(day);
      days.push({ day, title });
    }
  }

  if (days.length > 0) return days.sort((a, b) => a.day - b.day);

  // Fallback: generic Day 1..N from an explicit total-days marker.
  const totalMatch = text.match(/Total\s+Instructional\s+Days\s*:?\**\s*(\d+)/i);
  if (totalMatch) {
    const n = parseInt(totalMatch[1], 10);
    if (n > 0 && n <= 200) {
      for (let d = 1; d <= n; d += 1) days.push({ day: d, title: '' });
    }
  }
  return days;
}

/**
 * True when the CDD is DLU/day-based (a day schedule could be parsed).
 * Standard CDDs have no day table nor total-days marker -> false.
 */
export function detectDluCdd(cddContent) {
  return parseDaySchedule(cddContent).length > 0;
}

/**
 * Build Day options for the Blueprint dropdown, mirroring buildModuleOptions.
 * @returns {{ label: string, key: number, isDay: true, title: string }[]}
 */
export function buildDayOptions(cddContent, existingModuleNumbers = new Set()) {
  const days = parseDaySchedule(cddContent);
  return days.map(({ day, title }) => {
    const done = existingModuleNumbers.has(day) ? ' ✓' : '';
    const label = title ? `Day ${day}: ${title}${done}` : `Day ${day}${done}`;
    return { label, key: day, isDay: true, title };
  });
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
  isDay,
  dayTitle,
  extraInstructions,
}) {
  const extra = (extraInstructions || '').trim();
  const extraPart = extra ? `\n\n**Additional Instructions:**\n${extra}` : '';

  if (isDay) {
    const titlePart = dayTitle ? ` (${dayTitle})` : '';
    return (
      `**This is Day ${moduleNum}${titlePart} of the block.**\n`
      + `Generate the DLU (Daily Learning Unit) blueprint specifically for Day ${moduleNum}.`
      + extraPart
    );
  }

  if (isCourseEnd && courseEndLabel) {
    return (
      `**This is a Title-Level End item: '${courseEndLabel}'.**\n`
      + 'Generate the Blueprint specifically for this title-end item. '
      + 'It should align to all modules in the title.'
      + extraPart
    );
  }
  return (
    `**This is Module ${moduleNum} of the title.**\n`
    + `Generate the Blueprint specifically for Module ${moduleNum}. `
    + 'Lesson numbering should start from Lesson 1 within this module.'
    + extraPart
  );
}
