import { describe, it, expect } from 'vitest';
import {
  DOCUMENT_HEADER_SECTION_TITLE,
  WHOLE_DOCUMENT_SECTION_TITLE,
  buildBlueprintUiSections,
  buildHeadingFallbackSections,
  parseSectionsFromText,
  replaceHeadingSection,
} from '../blueprintContent';

/**
 * The real shape that sent a Day 9 outline to the whole-document fallback:
 * `blueprint_versions.id = 394` is 15 × "### " and 0 × "## ", so the `## `
 * parser found nothing and a 250-line document could only be regenerated in one
 * piece. Trimmed here, structure preserved exactly.
 */
const H3_DOC = [
  '### Part I — DLU-Wide Information',
  '',
  '**A. DLU Metadata**',
  '',
  '- **Block / Day:** Block 2 / Day 9',
  '- **Day Type:** Teaching Day',
  '',
  '**B. ACS Coverage**',
  '',
  '| ACS Code | Type |',
  '|---|---|',
  '| AM.I.E.K1 | Knowledge |',
  '',
  '### Part II — Scope & Sequence / List of Screens',
  '',
  '**Scope & Sequence / List of Screens**',
  '',
  "### Screen: Today's Mission",
  '',
  'Frame the day.',
  '',
  '### Screen: Quick Check',
  '',
  '- Item one',
  '- Item two',
  '',
  '### Screen: Day Reflection',
  '',
  'Close the day.',
].join('\n');

const H1_DOC = [
  '# Overview of the Day',
  '',
  'Some framing prose.',
  '',
  '# Screens',
  '',
  'A list of screens.',
].join('\n');

const H1_TITLE_PLUS_H3 = [
  '# Day 4 Blueprint',
  '',
  'Generated 2026-08-25.',
  '',
  '### First Screen',
  '',
  'Body one.',
  '',
  '### Second Screen',
  '',
  'Body two.',
].join('\n');

const STD_H2_DOC = [
  '## Module Purpose',
  '',
  'Teach the thing.',
  '',
  '## Lesson Structure',
  '',
  '- Topic A',
].join('\n');

const DUPLICATE_HEADINGS = [
  '### Screen: Quick Check',
  '',
  'First quick check body.',
  '',
  '### Screen: Quick Check',
  '',
  'Second quick check body.',
].join('\n');

/** Every `### ` heading line in a document, in order. */
function headingLines(text, level = 3) {
  const re = new RegExp(`^#{${level}}\\s+\\S`);
  return text.split('\n').filter((l) => re.test(l));
}

function sectionsOf(text) {
  return buildBlueprintUiSections(text, null);
}

function byTitle(secs, title) {
  const hit = secs.find((s) => s.title === title);
  expect(hit, `no section titled "${title}"`).toBeTruthy();
  return hit;
}

describe('heading fallback — which level wins', () => {
  it('sections a `### `-only document by its `### ` headings', () => {
    const secs = sectionsOf(H3_DOC);
    expect(secs.map((s) => s.title)).toEqual([
      'Part I — DLU-Wide Information',
      'Part II — Scope & Sequence / List of Screens',
      "Screen: Today's Mission",
      'Screen: Quick Check',
      'Screen: Day Reflection',
    ]);
    expect(secs.every((s) => s.heading)).toBe(true);
    expect(secs.every((s) => s.locator.level === 3)).toBe(true);
  });

  it('sections a `# `-only document by its `# ` headings', () => {
    const secs = sectionsOf(H1_DOC);
    expect(secs.map((s) => s.title)).toEqual(['Overview of the Day', 'Screens']);
    expect(secs.every((s) => s.locator.level === 1)).toBe(true);
  });

  it('prefers the deeper level when the shallow one is a lone title line', () => {
    // One `# Day 4 Blueprint` is a title, not an outline: it must lose the
    // two-heading floor so the real `### ` structure underneath is used.
    const secs = sectionsOf(H1_TITLE_PLUS_H3);
    expect(secs.every((s) => s.locator.level === 3)).toBe(true);
    expect(secs.map((s) => s.title)).toEqual([
      DOCUMENT_HEADER_SECTION_TITLE, 'First Screen', 'Second Screen',
    ]);
  });

  it('matches heading levels exactly — `#### ` is not a `### `', () => {
    const doc = '#### Sub A\n\nbody a\n\n#### Sub B\n\nbody b';
    const secs = sectionsOf(doc);
    expect(secs.map((s) => s.title)).toEqual(['Sub A', 'Sub B']);
    expect(secs.every((s) => s.locator.level === 4)).toBe(true);
  });

  it('splices `## ` documents by line range too, not by rebuilding them', () => {
    // This used to assert the opposite. `## ` was the one shape still rebuilt
    // from a parts dict on save, which rewrote the whole document — dropping the
    // preamble, flattening other heading levels and reordering sections — every
    // time one section was edited. It is also the shape the default
    // blueprint_generation prompt emits, so the common case carried the worst
    // save path. Titles are unchanged; only the write mechanism is.
    const secs = sectionsOf(STD_H2_DOC);
    expect(secs.every((s) => s.heading)).toBe(true);
    expect(secs.every((s) => s.locator.level === 2)).toBe(true);
    expect(secs.some((s) => s.whole)).toBe(false);
    expect(secs.map((s) => s.title)).toEqual(['Module Goal', 'Lesson Structure (Topics)']);
  });

  it('keeps a `## ` document byte-identical when a section is saved unedited', () => {
    // The property the rebuild could not have: an unedited save is a no-op.
    const secs = sectionsOf(STD_H2_DOC);
    secs.forEach((sec) => {
      expect(replaceHeadingSection(STD_H2_DOC, sec.locator, sec.content)).toBe(STD_H2_DOC);
    });
  });

  it('leaves a `## ` document with a re-derivable sections dict after a splice', () => {
    // BlueprintPage stores parseSectionsFromText(spliced) alongside the text.
    // It must not come back empty: promptops_app/core/shared.py looks labelled
    // snippets (Learning Objectives among them) up in that dict with no
    // full_content fallback, so an empty one silently starves downstream
    // generation. Verified across all 238 stored `## ` versions.
    const secs = sectionsOf(STD_H2_DOC);
    const spliced = replaceHeadingSection(STD_H2_DOC, secs[0].locator, 'NEW BODY');
    expect(Object.keys(parseSectionsFromText(spliced)))
      .toEqual(Object.keys(parseSectionsFromText(STD_H2_DOC)));
    expect(Object.keys(parseSectionsFromText(spliced)).length).toBeGreaterThan(0);
  });

  it('keeps text above the first `## ` heading editable and intact', () => {
    const doc = [
      '# Day 4 Blueprint', '', 'Generated 2026-08-26.', '',
      '## Module Goal', '', 'goal body', '',
      '## Lesson Structure', '', 'lesson body',
    ].join('\n');
    const secs = sectionsOf(doc);
    expect(secs[0].title).toBe(DOCUMENT_HEADER_SECTION_TITLE);
    // Editing a later section must not disturb the preamble — the rebuild
    // dropped everything above the first `## ` outright.
    const out = replaceHeadingSection(doc, secs[2].locator, 'new lesson body');
    expect(out).toContain('# Day 4 Blueprint');
    expect(out).toContain('Generated 2026-08-26.');
    expect(out).toContain('goal body');
    expect(out).toContain('new lesson body');
  });

  it('never re-offers `## ` sections the UI deliberately hides', () => {
    // Only hidden `## ` sections -> zero visible sections. The document must not
    // come back sectioned by another level with those hidden sections restored.
    const doc = '## Step 5: Blueprint Validation\n\nValidation complete.';
    const secs = sectionsOf(doc);
    expect(secs).toHaveLength(1);
    expect(secs[0].title).toBe(WHOLE_DOCUMENT_SECTION_TITLE);
  });

  it('filters hidden headings out of a heading-sectioned document', () => {
    const doc = [
      '### Real Screen One', '', 'body one', '',
      '### Step 5: Blueprint Validation', '', 'Validation complete.', '',
      '### Real Screen Two', '', 'body two',
    ].join('\n');
    const secs = sectionsOf(doc);
    expect(secs.map((s) => s.title)).toEqual(['Real Screen One', 'Real Screen Two']);
  });
});

describe('heading fallback — when it must stand aside', () => {
  it('does not claim a document with a single heading', () => {
    // One heading would produce one section that looks like the whole document
    // but silently excludes the preamble above it.
    const doc = '# Only Heading\n\nA preamble is not reachable from one section.';
    expect(buildHeadingFallbackSections(doc)).toEqual([]);
    expect(sectionsOf(doc)[0].title).toBe(WHOLE_DOCUMENT_SECTION_TITLE);
  });

  it('does not claim a document with no headings at all', () => {
    const doc = "I'm unable to create a blueprint because the sources are missing.";
    expect(buildHeadingFallbackSections(doc)).toEqual([]);
    expect(sectionsOf(doc)).toEqual([
      { title: WHOLE_DOCUMENT_SECTION_TITLE, content: doc, whole: true },
    ]);
  });

  it('ignores headings whose body is empty', () => {
    const doc = '### Empty One\n\n### Empty Two\n';
    expect(buildHeadingFallbackSections(doc)).toEqual([]);
  });

  it('returns [] rather than a section for blank content', () => {
    expect(buildHeadingFallbackSections('')).toEqual([]);
    expect(buildHeadingFallbackSections('  \n \n')).toEqual([]);
  });

  it('leaves the DLU parser in charge of a DLU day blueprint', () => {
    const dlu = [
      '### DLU Outline', '',
      "**Today's Mission**", '', 'Mission body.', '',
      '**Learn It**', '', 'Learn body.', '',
      '**Quick Check**', '', 'Check body.',
    ].join('\n');
    const secs = sectionsOf(dlu);
    expect(secs.every((s) => s.dlu)).toBe(true);
    expect(secs.some((s) => s.heading)).toBe(false);
  });
});

describe('heading fallback — the preamble stays editable', () => {
  it('exposes text above the first heading as its own section', () => {
    const pre = byTitle(sectionsOf(H1_TITLE_PLUS_H3), DOCUMENT_HEADER_SECTION_TITLE);
    expect(pre.content).toBe('# Day 4 Blueprint\n\nGenerated 2026-08-25.');
    expect(pre.locator.startLine).toBe(-1);
    expect(pre.locator.headingText).toBeNull();
  });

  it('replaces only the preamble, leaving every heading untouched', () => {
    const secs = sectionsOf(H1_TITLE_PLUS_H3);
    const pre = byTitle(secs, DOCUMENT_HEADER_SECTION_TITLE);
    const out = replaceHeadingSection(H1_TITLE_PLUS_H3, pre.locator, 'NEW PREAMBLE');
    expect(out.startsWith('NEW PREAMBLE\n')).toBe(true);
    expect(out).toContain('### First Screen\n\nBody one.');
    expect(out).toContain('### Second Screen\n\nBody two.');
    expect(out).not.toContain('# Day 4 Blueprint');
  });

  it('omits the preamble section when there is no text above the first heading', () => {
    expect(sectionsOf(H3_DOC).map((s) => s.title))
      .not.toContain(DOCUMENT_HEADER_SECTION_TITLE);
  });
});

describe('replaceHeadingSection — the splice is surgical', () => {
  it('is a byte-level no-op when the body is unchanged', () => {
    for (const sec of sectionsOf(H3_DOC)) {
      expect(replaceHeadingSection(H3_DOC, sec.locator, sec.content)).toBe(H3_DOC);
    }
  });

  it('is a no-op for a body that differs only in surrounding whitespace', () => {
    const sec = byTitle(sectionsOf(H3_DOC), 'Screen: Quick Check');
    expect(replaceHeadingSection(H3_DOC, sec.locator, `\n\n${sec.content}\n  `)).toBe(H3_DOC);
  });

  it('changes one body and nothing else', () => {
    const before = sectionsOf(H3_DOC);
    const target = byTitle(before, 'Screen: Quick Check');
    const out = replaceHeadingSection(H3_DOC, target.locator, 'ONLY THIS');
    const after = sectionsOf(out);

    expect(headingLines(out)).toEqual(headingLines(H3_DOC));
    expect(after.map((s) => s.title)).toEqual(before.map((s) => s.title));
    after.forEach((sec, i) => {
      expect(sec.content).toBe(sec.title === 'Screen: Quick Check' ? 'ONLY THIS' : before[i].content);
    });
  });

  it('never rewrites the heading line or its level', () => {
    const sec = byTitle(sectionsOf(H3_DOC), 'Part I — DLU-Wide Information');
    const out = replaceHeadingSection(H3_DOC, sec.locator, 'replaced');
    expect(out.split('\n')[0]).toBe('### Part I — DLU-Wide Information');
    // No heading anywhere was demoted to `## `, which is what rebuilding the
    // document from its parsed sections would have done to all of them.
    expect(out.split('\n').filter((l) => /^##\s+\S/.test(l))).toEqual([]);
  });

  it('fences the new body with blank lines so markdown still parses', () => {
    const sec = byTitle(sectionsOf(H3_DOC), 'Screen: Quick Check');
    const out = replaceHeadingSection(H3_DOC, sec.locator, '- a\n- b');
    expect(out).toContain('### Screen: Quick Check\n\n- a\n- b\n\n### Screen: Day Reflection');
  });

  it('handles the last section, which no heading follows', () => {
    const secs = sectionsOf(H3_DOC);
    const last = secs[secs.length - 1];
    expect(last.locator.endHeadingText).toBeNull();
    const out = replaceHeadingSection(H3_DOC, last.locator, 'THE END');
    expect(out.trimEnd().endsWith('### Screen: Day Reflection\n\nTHE END')).toBe(true);
    expect(sectionsOf(out).map((s) => s.title)).toEqual(secs.map((s) => s.title));
  });

  it('keeps the heading when the body is emptied', () => {
    const sec = byTitle(sectionsOf(H3_DOC), 'Screen: Quick Check');
    const out = replaceHeadingSection(H3_DOC, sec.locator, '   ');
    expect(out).toContain('### Screen: Quick Check');
    expect(out).toContain('### Screen: Day Reflection');
    expect(out).not.toContain('- Item one');
  });
});

describe('replaceHeadingSection — duplicate headings are separate sections', () => {
  it('gives duplicate titles distinct identities', () => {
    const secs = sectionsOf(DUPLICATE_HEADINGS);
    expect(secs).toHaveLength(2);
    expect(secs[0].title).toBe(secs[1].title);
    expect(secs[0].key).not.toBe(secs[1].key);
    expect(secs.map((s) => s.content)).toEqual([
      'First quick check body.', 'Second quick check body.',
    ]);
  });

  it('edits the second duplicate without touching the first', () => {
    const secs = sectionsOf(DUPLICATE_HEADINGS);
    const out = replaceHeadingSection(DUPLICATE_HEADINGS, secs[1].locator, 'SECOND ONLY');
    expect(sectionsOf(out).map((s) => s.content)).toEqual([
      'First quick check body.', 'SECOND ONLY',
    ]);
  });
});

describe('replaceHeadingSection — a stale locator never splices blind', () => {
  it('re-finds a section whose line numbers have shifted', () => {
    const secs = sectionsOf(H3_DOC);
    const target = byTitle(secs, 'Screen: Quick Check');
    // The document grew above the section, so every recorded index is now wrong.
    const shifted = `Two\nextra\nlines\nadded\nup\ntop\n\n${H3_DOC}`;
    const out = replaceHeadingSection(shifted, target.locator, 'RESCUED');
    expect(out).not.toBeNull();
    expect(out).toContain('### Screen: Quick Check\n\nRESCUED');
    expect(out).not.toContain('- Item one');
    expect(byTitle(sectionsOf(out), 'Screen: Day Reflection').content).toBe('Close the day.');
  });

  it('refuses when the heading is gone from the document being saved', () => {
    const target = byTitle(sectionsOf(H3_DOC), 'Screen: Quick Check');
    const without = H3_DOC.replace('### Screen: Quick Check', '### Screen: Renamed Entirely');
    expect(replaceHeadingSection(without, target.locator, 'x')).toBeNull();
  });

  it('refuses when a drifted heading is no longer unique', () => {
    const target = byTitle(sectionsOf(H3_DOC), 'Screen: Quick Check');
    const stale = { ...target.locator, startLine: target.locator.startLine + 40 };
    const twice = `${H3_DOC}\n\n### Screen: Quick Check\n\nA second one appeared.`;
    expect(replaceHeadingSection(twice, stale, 'x')).toBeNull();
  });

  it('refuses a locator that is not a locator', () => {
    expect(replaceHeadingSection(H3_DOC, null, 'x')).toBeNull();
    expect(replaceHeadingSection(H3_DOC, {}, 'x')).toBeNull();
  });
});

describe('the contract the save path depends on', () => {
  it('every heading section carries the flag and the locator the splice needs', () => {
    for (const sec of sectionsOf(H3_DOC)) {
      expect(sec.heading).toBe(true);
      expect(sec.dlu).toBeUndefined();
      expect(sec.whole).toBeUndefined();
      expect(typeof sec.key).toBe('string');
      expect(typeof sec.locator.level).toBe('number');
      expect(typeof sec.locator.endLine).toBe('number');
    }
  });

  it('keeps section content verbatim so a save cannot delete UI-hidden lines', () => {
    // The `## ` path strips these for display and rebuilds from the stripped
    // text; this path splices the draft straight back, so stripping here would
    // delete the lines from the stored document on the first save.
    const doc = [
      '### Screen One', '', 'body one', '✅ Validation complete.', '',
      '### Screen Two', '', 'body two',
    ].join('\n');
    const secs = sectionsOf(doc);
    expect(byTitle(secs, 'Screen One').content).toContain('Validation complete');
    expect(replaceHeadingSection(doc, secs[0].locator, secs[0].content)).toBe(doc);
  });
});
