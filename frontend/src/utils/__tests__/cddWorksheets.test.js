import { describe, expect, it } from 'vitest';
import {
  detectDluCddContent,
  parseCddWorksheets,
  replaceWorksheet,
  buildDluWorksheetBlocks,
} from '@utils/cddWorksheets';

const DLU_CS = `# BLOCK 2 BLUEPRINT
## Sample Block

**Project:** Test Project

## WORKSHEET 1: BLOCK OVERVIEW
Overview body line one.
Overview body line two.

## WORKSHEET 2: INSTRUCTIONAL SEQUENCE MAP
| Day | Day Title | ACS Code |
|-----|-----------|----------|
| 1 | Intro | AM.I.E.K1 |
| 2 | Next | AM.I.E.K2 |

## WORKSHEET 3: ACS CODE REGISTRY
Some prose describing coverage.`;

const STANDARD_CS = `## Course Details
A standard CDD with modules.

## Course Structure
Module 1: Intro
Module 2: Advanced`;

// ── Shared format fixtures ───────────────────────────────────────────────────
// The worksheet label format is set by whichever prompt generated the CDD, so
// detection must not depend on markdown heading syntax. These mirror the cases
// in tests/characterization/test_dlu_cdd_export.py — keep the two in sync.

// Real shape produced by the "Block Blueprint" prompt from 2026-07-30 (CDDs
// 74-77): bold labels, zero markdown headings. Used to render as a flat blob.
const BOLD_CS = `**Block Blueprint for Block 2: Aircraft Drawings**

---

**Worksheet 1: Block Overview**

- **Block Title:** Aircraft Drawings
- **Domain:** General Studies

---

**Worksheet 2: Source File Inventory**

- **Course Syllabus/Calendar:** Available
- **ACS Codes:** Available

---

**Worksheet 3: ACS Code Registry**

- **AM.I.E.S6:** Skill-type ACS code`;

const PLAIN_CS = `Block Overview Document

Worksheet 1 — Block Overview
Body one.

Worksheet 2 . Source File Inventory
Body two.`;

// A standard CDD that merely mentions a worksheet in prose must not flip to DLU.
const PROSE_MENTION_CS = `## Course Structure
Module 1: Intro. Learners complete Worksheet 1 before the lab session, as
recorded in Worksheet 1 of the prior block.
Module 2: Advanced`;

// Table of contents printed before the real worksheets — naive splitting would
// emit near-empty worksheets at the TOC lines.
const TOC_CS = `# Block Blueprint

- **Worksheet 1: Block Overview**
- **Worksheet 2: Source File Inventory**

---

**Worksheet 1: Block Overview**

The real overview body, which is substantially longer than the TOC entry.

**Worksheet 2: Source File Inventory**

The real inventory body, also substantially longer than its TOC entry.`;

describe('cddWorksheets — detection', () => {
  it('detects worksheet-based (DLU) content', () => {
    expect(detectDluCddContent(DLU_CS)).toBe(true);
  });
  it('does not flag a standard CDD as DLU', () => {
    expect(detectDluCddContent(STANDARD_CS)).toBe(false);
    expect(detectDluCddContent('')).toBe(false);
  });
  it('accepts non-heading label formats — the prompt decides the format', () => {
    expect(detectDluCddContent(BOLD_CS)).toBe(true);
    expect(detectDluCddContent(PLAIN_CS)).toBe(true);
  });
  it('ignores prose mentions of a worksheet', () => {
    expect(detectDluCddContent(PROSE_MENTION_CS)).toBe(false);
    // A single bold label on its own is not enough evidence.
    expect(detectDluCddContent('Intro\n\n**Worksheet 1: Only One**\n\nBody.')).toBe(false);
    // ...but a single markdown heading is (the original, unchanged rule).
    expect(detectDluCddContent('Intro\n\n## WORKSHEET 1: ONLY ONE\n\nBody.')).toBe(true);
  });
});

describe('cddWorksheets — bold-label parsing (2026-07-30 regression)', () => {
  it('splits bold-labelled worksheets and keeps the label verbatim', () => {
    const { overview, worksheets } = parseCddWorksheets(BOLD_CS);
    expect(overview).toContain('Block Blueprint for Block 2');
    expect(worksheets).toHaveLength(3);
    expect(worksheets.map((w) => w.num)).toEqual([1, 2, 3]);
    expect(worksheets[0].content.startsWith('**Worksheet 1: Block Overview**')).toBe(true);
    expect(worksheets[1].content).toContain('Course Syllabus/Calendar');
    expect(worksheets[2].content).toContain('AM.I.E.S6');
    // Tab labels are cleaned of decoration, acronyms preserved.
    expect(worksheets[0].label).toBe('1. Block Overview');
    expect(worksheets[2].label).toContain('ACS');
  });

  it('round-trips a bold-labelled worksheet through replaceWorksheet', () => {
    const rebuilt = replaceWorksheet(BOLD_CS, 'worksheet_2', 'Regenerated body, no label.');
    const { worksheets } = parseCddWorksheets(rebuilt);
    expect(worksheets).toHaveLength(3);
    expect(worksheets.map((w) => w.num)).toEqual([1, 2, 3]);
    expect(worksheets[1].content).toContain('Regenerated body');
    // The original bold label was re-prepended, not a markdown heading.
    expect(worksheets[1].content.startsWith('**Worksheet 2: Source File Inventory**')).toBe(true);
    // Neighbours untouched.
    expect(worksheets[0].content).toContain('Block Title');
    expect(worksheets[2].content).toContain('AM.I.E.S6');
  });

  it('prefers the real section over a table-of-contents entry', () => {
    const { worksheets } = parseCddWorksheets(TOC_CS);
    expect(worksheets).toHaveLength(2);
    expect(worksheets[0].content).toContain('real overview body');
    expect(worksheets[1].content).toContain('real inventory body');
  });
});

describe('cddWorksheets — parse', () => {
  it('splits into an overview + one block per worksheet, heading kept in content', () => {
    const { overview, worksheets } = parseCddWorksheets(DLU_CS);
    expect(overview).toContain('BLOCK 2 BLUEPRINT');
    expect(worksheets).toHaveLength(3);
    expect(worksheets.map((w) => w.num)).toEqual([1, 2, 3]);
    // each worksheet block retains its own "## WORKSHEET N:" boundary heading
    worksheets.forEach((w) => {
      expect(w.content).toMatch(/^#{1,3}\s*WORKSHEET\s+\d+/i);
    });
  });

  it('round-trips: parse -> reconstruct(same) -> parse yields identical worksheets', () => {
    const { worksheets } = parseCddWorksheets(DLU_CS);
    let cs = DLU_CS;
    worksheets.forEach((w) => { cs = replaceWorksheet(cs, w.key, w.content); });
    const again = parseCddWorksheets(cs);
    expect(again.worksheets).toHaveLength(worksheets.length);
    expect(again.worksheets.map((w) => w.title)).toEqual(worksheets.map((w) => w.title));
  });
});

describe('cddWorksheets — replaceWorksheet heading preservation (regen guard)', () => {
  it('re-prepends the worksheet heading when regenerated content omits it', () => {
    // The section-regenerate prompt returns body-only content (no heading).
    const regenBody = 'Freshly regenerated worksheet 2 content, no heading.';
    const rebuilt = replaceWorksheet(DLU_CS, 'worksheet_2', regenBody);

    const { worksheets } = parseCddWorksheets(rebuilt);
    // All three worksheets must still be present with intact boundaries.
    expect(worksheets).toHaveLength(3);
    expect(worksheets.map((w) => w.num)).toEqual([1, 2, 3]);
    // Worksheet 2 kept its heading and got the new body.
    expect(worksheets[1].title).toMatch(/WORKSHEET 2/i);
    expect(worksheets[1].content).toContain('Freshly regenerated worksheet 2');
    expect(worksheets[1].content).toMatch(/^#{1,3}\s*WORKSHEET\s+2/i);
    // Neighbours untouched.
    expect(worksheets[0].content).toContain('Overview body');
    expect(worksheets[2].content).toContain('coverage');
  });

  it('does not double-add a heading when content already has one (manual edit path)', () => {
    const edited = '## WORKSHEET 2: INSTRUCTIONAL SEQUENCE MAP\nEdited body.';
    const rebuilt = replaceWorksheet(DLU_CS, 'worksheet_2', edited);
    const { worksheets } = parseCddWorksheets(rebuilt);
    expect(worksheets).toHaveLength(3);
    const w2 = worksheets[1].content;
    expect((w2.match(/WORKSHEET 2/gi) || [])).toHaveLength(1);
  });
});

describe('cddWorksheets — buildDluWorksheetBlocks', () => {
  it('builds Overview + worksheet blocks with the folder icon', () => {
    const { blocks } = buildDluWorksheetBlocks(DLU_CS, { 'Course Structure': DLU_CS });
    expect(blocks[0].label).toContain('Overview');
    expect(blocks.length).toBe(4); // overview + 3 worksheets
    blocks.slice(1).forEach((b) => expect(b.label.startsWith('🗂️')).toBe(true));
  });
});
