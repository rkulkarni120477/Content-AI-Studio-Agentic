import { describe, expect, it } from 'vitest';
import {
  detectDluCdd,
  buildDayOptions,
  buildModuleOptions,
} from '@utils/blueprintModules';

// Older Instructional Sequence Map shape: explicit "Day Title" column.
const LEGACY_DAY_TABLE = `
## Instructional Sequence Map

| Day | Day Title | Notes |
|---|---|---|
| 1 | Introduction to Aircraft Drawings | foo |
| 2 | Basic Materials and Processes | bar |
`;

// Block-wide digest pipeline's Day-by-Day Map shape: "Day" + "Topic" (no
// literal "Day Title" column) — promptops_app/services/block_wide_service.py
// _DAY_TABLE_HEADER. The Day CELL is "Day 1", not "1": that pipeline writes the
// label form deliberately, matched cell-by-cell against the AIM reference (see
// `day_label` in _day_table_from_rows). This fixture previously used bare numbers,
// so it passed while every real block-wide CDD failed to parse — the Blueprint page
// fell back to "Module 1" for a 20-day block.
const DIGEST_PIPELINE_DAY_TABLE = `
## WORKSHEET 3: DAY-BY-DAY MAP

| Day | Topic | Handbook Reference | ACS | Concept Type | Notes |
|---|---|---|---|---|---|
| Day 1 | Introduction to Aircraft Drawings | FAA-H-8083-30B | AM.I.B.K1 | Conceptual | n/a |
| Day 2 | Basic Materials and Processes | FAA-H-8083-30B | AM.I.B.K2 | Procedural | n/a |
`;

// The same shape with bare numeric day cells, which the legacy tables and any
// hand-authored CDD may still use. Both forms must parse.
const NUMERIC_DAY_CELL_TABLE = `
| Day | Topic | Notes |
|---|---|---|
| 1 | Introduction | n/a |
| 2 | Fundamentals | n/a |
`;

// A dayless row renders its Day cell as an em dash, and a summary row may cover a
// RANGE. Neither is a day, and neither may become one.
const NON_DAY_ROWS_TABLE = `
| Day | Topic | Notes |
|---|---|---|
| Day 1 | Introduction | n/a |
| — | Unscheduled material | no day resolved |
| Days 2-3 | Combined review | range, not a day |
`;

const MODULE_CDD = `
Course Structure
Module 1: Introduction
Module 2: Fundamentals
`;

// A table that mentions "Day" but not as its first column should not be
// mistaken for a day schedule.
const UNRELATED_TABLE_WITH_DAY_COLUMN = `
| Assignment | Day | Score |
|---|---|---|
| HW1 | Monday | 90 |
`;

// A standard Module CDD's own "Suggested Pacing" table: "Day" as the FIRST
// column (just like the real digest-pipeline shape), but the second column is
// NOT "Topic" — this must not be mistaken for a day schedule, or a Module CDD
// with a pacing table would wrongly lose its Module selector entirely and be
// forced into "Select Day" with no actual days.
const MODULE_CDD_WITH_PACING_TABLE = `
Course Structure
Module 1: Introduction
Module 2: Fundamentals

## Suggested Pacing
| Day | Activity | Duration |
|---|---|---|
| 1 | Read chapter 1 | 2h |
| 2 | Lab exercise | 1h |
`;

describe('detectDluCdd', () => {
  it('detects the legacy "Day Title" schedule table', () => {
    expect(detectDluCdd(LEGACY_DAY_TABLE)).toBe(true);
  });

  it('detects the digest pipeline\'s Day-by-Day Map table (Day + Topic, no "Day Title" column)', () => {
    expect(detectDluCdd(DIGEST_PIPELINE_DAY_TABLE)).toBe(true);
  });

  it('does not flag a standard module CDD as day-based', () => {
    expect(detectDluCdd(MODULE_CDD)).toBe(false);
  });

  it('falls back to a "Total Instructional Days" marker when no table is present', () => {
    expect(detectDluCdd('Total Instructional Days: 5')).toBe(true);
  });

  it('does not misdetect a table where "Day" is not the first column', () => {
    expect(detectDluCdd(UNRELATED_TABLE_WITH_DAY_COLUMN)).toBe(false);
  });

  it('does not misdetect a Module CDD\'s own "Day"-first-column pacing table as a day schedule', () => {
    expect(detectDluCdd(MODULE_CDD_WITH_PACING_TABLE)).toBe(false);
  });
});

describe('buildModuleOptions with a pacing table present', () => {
  it('still builds Module options, not Day options, when the CDD has a pacing table', () => {
    const options = buildModuleOptions(MODULE_CDD_WITH_PACING_TABLE);
    expect(options.map((o) => o.label)).toEqual([
      'Module 1: Introduction',
      'Module 2: Fundamentals',
    ]);
  });
});

describe('buildDayOptions', () => {
  it('builds Day 1..N options with titles from the digest pipeline table', () => {
    const options = buildDayOptions(DIGEST_PIPELINE_DAY_TABLE);
    expect(options).toEqual([
      { label: 'Day 1: Introduction to Aircraft Drawings', key: 1, isDay: true, title: 'Introduction to Aircraft Drawings' },
      { label: 'Day 2: Basic Materials and Processes', key: 2, isDay: true, title: 'Basic Materials and Processes' },
    ]);
  });

  it('reads a bare numeric Day cell too, so legacy and hand-authored tables still parse', () => {
    expect(buildDayOptions(NUMERIC_DAY_CELL_TABLE).map((o) => o.key)).toEqual([1, 2]);
  });

  it('ignores an em-dash placeholder row and a day RANGE row', () => {
    const options = buildDayOptions(NON_DAY_ROWS_TABLE);
    expect(options.map((o) => o.key)).toEqual([1]);
  });

  it('tolerates a bolded Day cell', () => {
    const bolded = ['| Day | Topic |', '|---|---|', '| **Day 4** | Wiring |'].join('\n');
    expect(buildDayOptions(bolded).map((o) => o.key)).toEqual([4]);
  });
});

describe('detectDluCdd on the real block-wide shape', () => {
  it('detects a day schedule whose cells are written as "Day N"', () => {
    expect(detectDluCdd(DIGEST_PIPELINE_DAY_TABLE)).toBe(true);
  });
});

describe('buildModuleOptions', () => {
  it('still builds Module options for a standard module CDD', () => {
    const options = buildModuleOptions(MODULE_CDD);
    expect(options.map((o) => o.label)).toEqual([
      'Module 1: Introduction',
      'Module 2: Fundamentals',
    ]);
  });
});
