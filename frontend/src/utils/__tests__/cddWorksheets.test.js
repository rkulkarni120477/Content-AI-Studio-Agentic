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

describe('cddWorksheets — detection', () => {
  it('detects worksheet-based (DLU) content', () => {
    expect(detectDluCddContent(DLU_CS)).toBe(true);
  });
  it('does not flag a standard CDD as DLU', () => {
    expect(detectDluCddContent(STANDARD_CS)).toBe(false);
    expect(detectDluCddContent('')).toBe(false);
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
