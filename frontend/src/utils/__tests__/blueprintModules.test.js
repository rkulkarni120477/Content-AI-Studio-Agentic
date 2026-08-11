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
// _DAY_TABLE_HEADER.
const DIGEST_PIPELINE_DAY_TABLE = `
## WORKSHEET 3: DAY-BY-DAY MAP

| Day | Topic | Handbook Reference | ACS | Concept Type | Notes |
|---|---|---|---|---|---|
| 1 | Introduction to Aircraft Drawings | FAA-H-8083-30B | AM.I.B.K1 | Conceptual | n/a |
| 2 | Basic Materials and Processes | FAA-H-8083-30B | AM.I.B.K2 | Procedural | n/a |
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
