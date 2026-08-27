import { describe, expect, it } from 'vitest';
import {
  buildLabels,
  describeOverrides,
  lowerFirst,
  pluralize,
  sanitizeOverrides,
  applyTerminology,
  DEFAULT_LABELS,
} from '../tenantLabels';

describe('pluralize', () => {
  it('adds -s to ordinary words', () => {
    expect(pluralize('Style')).toBe('Styles');
    expect(pluralize('Block')).toBe('Blocks');
    expect(pluralize('Design Guide')).toBe('Design Guides');
  });

  it('adds -es after a sibilant', () => {
    expect(pluralize('Class')).toBe('Classes');
    expect(pluralize('Batch')).toBe('Batches');
  });

  it('turns consonant + y into -ies', () => {
    expect(pluralize('Story')).toBe('Stories');
  });

  it('leaves vowel + y alone', () => {
    expect(pluralize('Play')).toBe('Plays');
  });

  it('adds a plain -s to acronyms', () => {
    expect(pluralize('CDD')).toBe('CDDs');
  });
});

describe('lowerFirst', () => {
  it('lowers only the first character', () => {
    expect(lowerFirst('Design Guide')).toBe('design Guide');
  });

  it('leaves acronyms uppercase so mid-sentence use reads correctly', () => {
    expect(lowerFirst('CDD')).toBe('CDD');
  });
});

describe('sanitizeOverrides', () => {
  it('drops unknown keys', () => {
    expect(sanitizeOverrides({ style: 'Guide', bogus: 'x' })).toEqual({ style: 'Guide' });
  });

  it('drops blank and whitespace-only values', () => {
    expect(sanitizeOverrides({ style: '   ', cdd: '' })).toEqual({});
  });

  it('drops values equal to the default, so they are not shown as customized', () => {
    expect(sanitizeOverrides({ style: 'Style' })).toEqual({});
  });

  it('trims and caps overlong input', () => {
    expect(sanitizeOverrides({ style: '  Guide  ' })).toEqual({ style: 'Guide' });
    expect(sanitizeOverrides({ style: 'B'.repeat(60) }).style).toHaveLength(40);
  });

  it('tolerates null, undefined and non-objects', () => {
    expect(sanitizeOverrides(null)).toEqual({});
    expect(sanitizeOverrides(undefined)).toEqual({});
    expect(sanitizeOverrides('nope')).toEqual({});
  });
});

describe('buildLabels', () => {
  it('falls back to defaults when there are no overrides', () => {
    const L = buildLabels({});
    expect(L.title).toBe('Title');
    expect(L.style).toBe('Style');
    expect(L.cdd).toBe('CDD');
    expect(L.blueprint).toBe('Blueprint');
    expect(L.isCustomized).toBe(false);
  });

  it('applies an override across every word form', () => {
    const L = buildLabels({ style: 'Design Guide' });
    expect(L.style).toBe('Design Guide');
    expect(L.styles).toBe('Design Guides');
    expect(L.styleLower).toBe('design Guide');
    expect(L.stylesLower).toBe('design Guides');
    expect(L.isCustomized).toBe(true);
  });

  it('leaves untouched keys on their defaults', () => {
    const L = buildLabels({ title: 'Block' });
    expect(L.title).toBe('Block');
    expect(L.titles).toBe('Blocks');
    expect(L.style).toBe('Style');
    expect(L.cdd).toBe('CDD');
  });

  it('never yields an empty label, whatever the input', () => {
    [null, undefined, {}, { style: '' }, { style: '   ' }, 'garbage', 42].forEach((input) => {
      const L = buildLabels(input);
      Object.keys(DEFAULT_LABELS).forEach((key) => {
        expect(L[key]).toBeTruthy();
        expect(L[`${key}s`]).toBeTruthy();
        expect(L[`${key}Lower`]).toBeTruthy();
      });
    });
  });
});

describe('describeOverrides', () => {
  it('lists only the renamed labels', () => {
    expect(describeOverrides({ title: 'Block', style: 'Design Guide' })).toEqual([
      { key: 'title', from: 'Title', to: 'Block' },
      { key: 'style', from: 'Style', to: 'Design Guide' },
    ]);
  });

  it('returns nothing for a tenant on default wording', () => {
    expect(describeOverrides({})).toEqual([]);
    expect(describeOverrides({ style: 'Style' })).toEqual([]);
  });
});

describe('applyTerminology', () => {
  const L = buildLabels({
    title: 'Course',
    style: 'Design Guide',
    blueprint: 'Learning Plan',
  });

  it('rewrites whole words in generated copy', () => {
    expect(applyTerminology('create a detailed module blueprint', L, ['blueprint']))
      .toBe('create a detailed module learning Plan');
    expect(applyTerminology('defining a style for content', L, ['style']))
      .toBe('defining a design Guide for content');
  });

  it('does not rewrite snake_case identifiers by default', () => {
    expect(applyTerminology('default_style_prompt', L, ['style'])).toBe('default_style_prompt');
    expect(applyTerminology('default_blueprint_prompt', L, ['blueprint'])).toBe('default_blueprint_prompt');
  });

  it('rewrites snake_case identifiers when asked', () => {
    expect(applyTerminology('default_style_prompt', L, ['style'], { identifiers: true }))
      .toBe('default_Design Guide_prompt');
    expect(applyTerminology('default_blueprint_prompt', L, ['blueprint'], { identifiers: true }))
      .toBe('default_Learning Plan_prompt');
  });
});
