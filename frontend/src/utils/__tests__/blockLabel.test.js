import { describe, it, expect } from 'vitest';
import { inferBlockLabel } from '@utils/blockLabel';

describe('inferBlockLabel', () => {
  it('extracts the block from prompt prose', () => {
    // The real AIM Block 2 prompt opens with exactly this shape.
    expect(inferBlockLabel(
      'You are an SME and instructional designer producing a Block Blueprint for '
      + 'Block 2 — Aircraft Drawings, Materials and Processes, Cleaning and Corrosion Control',
    )).toBe('Block 2');
  });

  it('normalises casing, padding, and the BLK abbreviation', () => {
    // All of these must key the SAME calendar row as "Block 2" — the label is
    // matched with SQL equality against the stored value, so normalising is the
    // entire point of returning a canonical string rather than the raw match.
    for (const raw of ['block 2', 'BLOCK 2', 'Block 02', 'block2', 'BLK 2', 'blk 02']) {
      expect(inferBlockLabel(raw)).toBe('Block 2');
    }
  });

  it('honours argument order — earlier sources win', () => {
    // Callers pass most-specific first (selected prompt → CDD → course), because a
    // block-specific prompt is a stronger signal than a generically-named course.
    expect(inferBlockLabel('Block 6 handbook', 'Block 2 course')).toBe('Block 6');
  });

  it('skips empty and non-string sources without consuming priority', () => {
    expect(inferBlockLabel(null, undefined, '', 0, {}, 'Block 11 review')).toBe('Block 11');
  });

  it('returns null rather than guessing when nothing matches', () => {
    // A wrong label enumerates the wrong calendar, so "no match" must stay empty
    // and let the user type it — never fall back to a default.
    expect(inferBlockLabel('Physics', 'Intro to Airframes', '')).toBeNull();
    expect(inferBlockLabel()).toBeNull();
  });

  it('does not match a bare number or an unrelated word ending in "block"', () => {
    expect(inferBlockLabel('Module 2')).toBeNull();
    expect(inferBlockLabel('2')).toBeNull();
    // \b prevents "roadblock 3" / "titleblock 4" from reading as a block label —
    // "title block" is real Block 2 subject matter, so this is a live risk.
    expect(inferBlockLabel('roadblock 3')).toBeNull();
    expect(inferBlockLabel('the titleblock 4 drawing')).toBeNull();
  });

  it('matches multi-digit blocks (AIM runs past Block 9)', () => {
    expect(inferBlockLabel('Block 11 - Instructor Guide')).toBe('Block 11');
  });
});
