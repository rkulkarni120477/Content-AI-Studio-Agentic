import { describe, expect, it } from 'vitest';
import {
  detectDluBlueprint,
  parseDluBlueprintSections,
  replaceDluBlueprintSection,
} from '@utils/dluBlueprint';
import {
  buildBlueprintUiSections,
  WHOLE_DOCUMENT_SECTION_TITLE,
} from '@utils/blueprintContent';

// Real shape of a DLU day blueprint (from blueprint 236 / 232).
const DLU = `**Day Number:** 1
**Day Type:** Teaching Day
**Topic:** Introduction to Aircraft Drawings
**Codes Addressed:** AM.I.B.K1, AM.I.B.K2, AM.I.B.K4

---

### DLU Outline

1. **Today's Mission**
   - Understanding aircraft drawings is crucial for maintenance.
2. **Learn It**
   - Structured Canvas page drawn from the FAA manuals.
3. **Quick Check**
   - 5-8 formative items, multiple attempts.
4. **Up Next in Class**
   - Preview of the hands-on activity.
5. **Day Reflection**
   - Two prompts answered in 90 seconds.`;

// Format B (bp 234): "### DLU Outline" + NON-numbered bold parts.
const DLU_BOLD = `**Day Number:** 5
**Day Type:** REVIEW DAY
**Topic:** Drawing Interpretation Review

---

### DLU Outline

**Today's Mission**
Today's session focuses on interpreting aircraft drawings.

**Learn It (Review Content)**
- **Topic Label:** Drawing Interpretation Review

**Quick Check**
- 5 items.

**Up Next in Class**
- Bench work.

**Day Reflection**
- Two prompts.`;

// Format C (bp 233): NO "### DLU Outline", bold-with-colon parts.
const DLU_COLON = `**Day Number:** 3
**Day Type:** Teaching Day
**Topic:** Sectional Views

---

**Today's Mission:**
Understanding aircraft drawings is crucial.

**Learn It:**
- **Topic Label:** Sectional views and detail drawings.

**Quick Check:**
- 6 items.

**Up Next in Class:**
- Hangar activity.

**Day Reflection:**
- Two prompts.`;

// Standard module blueprint — ## sections, no DLU markers.
const STD = `## Module Overview
This module introduces the topic.

## Lesson Structure (Topics)
1. **Lesson 1: Intro**
2. **Lesson 2: Build**`;

describe('dluBlueprint — detection', () => {
  it('detects a DLU day blueprint', () => {
    expect(detectDluBlueprint(DLU)).toBe(true);
  });
  it('does not flag a standard module blueprint', () => {
    expect(detectDluBlueprint(STD)).toBe(false);
    expect(detectDluBlueprint('')).toBe(false);
  });
  it('detects even without the "### DLU Outline" marker (>=2 named parts)', () => {
    const noHeader = "1. **Today's Mission**\n   - x\n2. **Day Reflection**\n   - y";
    expect(detectDluBlueprint(noHeader)).toBe(true);
  });
  it('detects the non-numbered bold format (bp 234)', () => {
    expect(detectDluBlueprint(DLU_BOLD)).toBe(true);
  });
  it('detects the bold-with-colon, no-heading format (bp 233)', () => {
    expect(detectDluBlueprint(DLU_COLON)).toBe(true);
  });
});

describe('dluBlueprint — the three real formats all yield the 5 parts', () => {
  const expected = ["Today's Mission", 'Learn It', 'Quick Check', 'Up Next in Class', 'Day Reflection'];
  for (const [name, fixture] of [['numbered', DLU], ['bold', DLU_BOLD], ['bold-colon', DLU_COLON]]) {
    it(`${name}: parses Overview + 5 DLU parts and does not treat "Topic Label" as a part`, () => {
      const secs = parseDluBlueprintSections(fixture);
      expect(secs[0].title).toBe('Overview');
      const partTitles = secs.slice(1).map((s) => s.title.replace(/\s*\(.*\)$/, ''));
      expect(partTitles).toEqual(expected);
      expect(secs.some((s) => /topic label/i.test(s.title))).toBe(false);
    });
    it(`${name}: round-trips a body-only regenerate keeping the header`, () => {
      const secs = parseDluBlueprintSections(fixture);
      const learn = secs.find((s) => /^learn it/i.test(s.title));
      const rebuilt = replaceDluBlueprintSection(fixture, learn.title, 'FRESH BODY.');
      const again = parseDluBlueprintSections(rebuilt);
      expect(again.length).toBe(secs.length);
      const l2 = again.find((s) => s.title === learn.title);
      expect(l2.content).toContain('FRESH BODY.');
      expect(l2.content.split('\n')[0]).toContain('Learn It');
    });
  }
});

describe('dluBlueprint — parse', () => {
  it('splits into Overview + one section per numbered DLU part', () => {
    const secs = parseDluBlueprintSections(DLU);
    expect(secs[0].title).toBe('Overview');
    expect(secs.map((s) => s.title)).toEqual([
      'Overview', "Today's Mission", 'Learn It', 'Quick Check', 'Up Next in Class', 'Day Reflection',
    ]);
    // Overview keeps the header + DLU Outline line.
    expect(secs[0].content).toContain('Day Number');
    expect(secs[0].content).toContain('### DLU Outline');
    // Each part keeps its own "N. **Title**" line verbatim.
    expect(secs[1].content.startsWith("1. **Today's Mission**")).toBe(true);
  });
});

describe('dluBlueprint — replace (splice-back)', () => {
  it('replaces one part and preserves the others + numbering', () => {
    const rebuilt = replaceDluBlueprintSection(DLU, 'Learn It', 'Regenerated Learn It body.');
    const secs = parseDluBlueprintSections(rebuilt);
    expect(secs.map((s) => s.title)).toEqual([
      'Overview', "Today's Mission", 'Learn It', 'Quick Check', 'Up Next in Class', 'Day Reflection',
    ]);
    // The edited part got the new body, with its "2. **Learn It**" label re-prepended.
    const learn = secs.find((s) => s.title === 'Learn It');
    expect(learn.content).toContain('Regenerated Learn It body.');
    expect(learn.content.startsWith('2. **Learn It**')).toBe(true);
    // Neighbours untouched.
    expect(secs.find((s) => s.title === "Today's Mission").content).toContain('crucial for maintenance');
    expect(secs.find((s) => s.title === 'Quick Check').content).toContain('formative items');
  });

  it('keeps a manually-edited part that already carries its label', () => {
    const edited = "3. **Quick Check**\n   - Edited items.";
    const rebuilt = replaceDluBlueprintSection(DLU, 'Quick Check', edited);
    const qc = parseDluBlueprintSections(rebuilt).find((s) => s.title === 'Quick Check');
    expect((qc.content.match(/Quick Check/g) || []).length).toBe(1); // no doubled label
    expect(qc.content).toContain('Edited items');
  });

  it('replaces the Overview block', () => {
    const rebuilt = replaceDluBlueprintSection(DLU, 'Overview', '**Day Number:** 1\n\n### DLU Outline');
    const secs = parseDluBlueprintSections(rebuilt);
    expect(secs).toHaveLength(6);
    expect(secs[0].title).toBe('Overview');
  });
});

describe('buildBlueprintUiSections — DLU vs standard', () => {
  it('returns DLU parts as sections for a DLU blueprint', () => {
    const ui = buildBlueprintUiSections(DLU, null);
    expect(ui.map((s) => s.title)).toContain("Today's Mission");
    expect(ui.length).toBe(6);
  });
  it('still parses a standard blueprint via ## sections (unchanged)', () => {
    const ui = buildBlueprintUiSections(STD, null);
    expect(ui.map((s) => s.title)).toContain('Module Overview');
    expect(ui.some((s) => s.title === "Today's Mission")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Shapes the DLU prompt does not pin. Its OUTPUT FORMAT asks for "a structured
// outline" and nothing about markdown, so the model picks the shape per run.
// Before these were recognised the page fell through to a read-only render and
// every control — Save, Regenerate Section, per-item ⟳ — silently vanished.
// ---------------------------------------------------------------------------

// Body on the same line as the bold part name.
const DLU_INLINE = `**Day Number:** 4
**Topic:** Exploded Views

---

**Today's Mission:** Understanding exploded views is crucial for maintenance.

**Learn It:** Interpreting exploded view drawings.
- **Topic Label:** Exploded Views and Assembly Diagrams

**Quick Check:** 5-8 formative items.
- **Quick Check Targeting:** Prioritise AM.I.B.S1 at APPLY level.

**Up Next in Class:** A hands-on bench activity.

**Day Reflection:** Two 90-second prompts.`;

// Markdown headings instead of bold lines.
const DLU_HEADING = `**Day Number:** 6
**Topic:** Tolerances

#### Today's Mission
Reading tolerance callouts correctly.

#### Learn It
- Limits and fits.

#### Quick Check
- 6 items.

#### Up Next in Class
- Gauge station.

#### Day Reflection
- Two prompts.`;

// Plain numbered lines, no bold at all.
const DLU_PLAIN = `**Day Number:** 7
**Topic:** Schematics

1. Today's Mission
   Reading electrical schematics.
2. Learn It
   - Symbols and buses.
3. Quick Check
   - 5 items.
4. Up Next in Class
   - Wiring bench.
5. Day Reflection
   - Two prompts.`;

describe('dluBlueprint — shapes the prompt leaves unpinned', () => {
  const expected = ["Today's Mission", 'Learn It', 'Quick Check', 'Up Next in Class', 'Day Reflection'];
  for (const [name, fixture] of [
    ['inline bold-with-colon', DLU_INLINE],
    ['markdown headings', DLU_HEADING],
    ['plain numbered', DLU_PLAIN],
  ]) {
    it(`${name}: detected and split into Overview + the 5 parts`, () => {
      expect(detectDluBlueprint(fixture)).toBe(true);
      const secs = parseDluBlueprintSections(fixture);
      expect(secs[0].title).toBe('Overview');
      expect(secs.slice(1).map((s) => s.title.replace(/\s*\(.*\)$/, ''))).toEqual(expected);
    });
    it(`${name}: yields editable UI sections rather than the whole-document fallback`, () => {
      const ui = buildBlueprintUiSections(fixture, null);
      expect(ui).toHaveLength(6);
      expect(ui.some((s) => s.whole)).toBe(false);
    });
  }

  it('re-prepends only the label when an inline part regenerates body-only', () => {
    const rebuilt = replaceDluBlueprintSection(DLU_INLINE, "Today's Mission", 'FRESH BODY.');
    const mission = parseDluBlueprintSections(rebuilt).find((s) => s.title === "Today's Mission");
    expect(mission.content).toContain('FRESH BODY.');
    expect(mission.content.split('\n')[0]).toBe("**Today's Mission:**");
    // The label carries no body text, so the replaced sentence is really gone.
    expect(mission.content).not.toContain('crucial for maintenance');
  });

  it('never promotes a body label that merely starts with a part name', () => {
    // "Quick Check Targeting" begins with "Quick Check". The relaxed passes match
    // part names whole for exactly this reason.
    for (const fixture of [DLU, DLU_INLINE]) {
      const titles = parseDluBlueprintSections(fixture).map((s) => s.title);
      expect(titles.some((t) => /targeting/i.test(t))).toBe(false);
      expect(titles).toHaveLength(6);
    }
  });

  it('leaves a standard module blueprint alone', () => {
    expect(detectDluBlueprint(STD)).toBe(false);
    expect(buildBlueprintUiSections(STD, null).some((s) => s.whole)).toBe(false);
  });

  it('prefers the strict pass, so a document that parsed before parses identically', () => {
    // DLU carries "1. **Today's Mission**" lines AND indented bullets; only the
    // strict pass may claim it, or the bullets would become part boundaries.
    expect(parseDluBlueprintSections(DLU).map((s) => s.title)).toEqual([
      'Overview', "Today's Mission", 'Learn It', 'Quick Check', 'Up Next in Class', 'Day Reflection',
    ]);
  });
});

describe('buildBlueprintUiSections — whole-document fallback', () => {
  const UNSTRUCTURED = 'A day outline with no headings, no bold part names and no numbering at all.';

  it('returns the document as one whole-document section when nothing parses', () => {
    const ui = buildBlueprintUiSections(UNSTRUCTURED, null);
    expect(ui).toHaveLength(1);
    expect(ui[0].title).toBe(WHOLE_DOCUMENT_SECTION_TITLE);
    expect(ui[0].whole).toBe(true);
    expect(ui[0].content).toBe(UNSTRUCTURED);
  });

  it('keeps the content verbatim so a save cannot drop UI-hidden lines', () => {
    const withHidden = `${UNSTRUCTURED}\n\n✅ Validation complete.`;
    expect(buildBlueprintUiSections(withHidden, null)[0].content).toContain('Validation complete');
  });

  it('still returns nothing for an empty document', () => {
    expect(buildBlueprintUiSections('', null)).toEqual([]);
    expect(buildBlueprintUiSections('   \n  ', null)).toEqual([]);
  });

  it('does not fire when the standard `## ` parser finds sections', () => {
    expect(buildBlueprintUiSections(STD, null).some((s) => s.whole)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// A `## `-sectioned document that happens to nest the five parts as `### `
// headings is NOT a DLU-shaped document. The DLU parser gives every line after
// the last part header to that part, so reshaping this one buries the trailing
// `## ` sections inside Day Reflection — where regenerating Day Reflection
// deletes them. Real shape: blueprints 223 and 224.
// ---------------------------------------------------------------------------
const NESTED_UNDER_SECTIONS = `# DLU OUTLINE — BLOCK 2, DAY 1
**Day Number:** 1
**Topic:** Introduction to Aircraft Drawings

## SECTION OUTLINES

### 1. TODAY'S MISSION
Framing for the day.

### 2. LEARN IT
Canvas page content.

### 3. QUICK CHECK
Five items.

### 4. UP NEXT IN CLASS
Bench preview.

### 5. DAY REFLECTION
Two prompts.

## INTERACTIVE/JOB AID NOTES
Storyline explorer.

## MASTER MECHANIC MOMENT STATUS
REQUIRES_ID_JUDGMENT.

## OPEN ITEMS
Handbook citation missing.

## SUMMARY
Outline complete.`;

// The canonical DLU outline puts its own `## ` blocks ABOVE the parts precisely
// so nothing structural follows them, which is what makes the reshape safe.
const APPENDIX_ABOVE_PARTS = `# DLU Outline - Day 4: Exploded Views
**Day Number:** 4

## ACS CODES ADDRESSED
| ACS Code | Type |
|---|---|
| AM.I.B.S1 | Skill |

## OPEN ITEMS
None identified.

---

### DLU Outline

1. **Today's Mission**
   Framing.
2. **Learn It**
   Content.
3. **Quick Check**
   Items.
4. **Up Next in Class**
   Preview.
5. **Day Reflection**
   Prompts.`;

describe('buildBlueprintUiSections — the DLU reshape must not swallow trailing sections', () => {
  it('keeps `## ` sections when they continue after the parts', () => {
    const ui = buildBlueprintUiSections(NESTED_UNDER_SECTIONS, null);
    expect(ui.map((s) => s.title)).toEqual([
      'SECTION OUTLINES',
      'INTERACTIVE/JOB AID NOTES',
      'MASTER MECHANIC MOMENT STATUS',
      'OPEN ITEMS',
      'SUMMARY',
    ]);
    // Nothing may claim to be a DLU part here — that flag drives the splice.
    expect(ui.some((s) => s.dlu)).toBe(false);
    expect(ui.some((s) => s.whole)).toBe(false);
  });

  it('keeps every trailing section independently editable', () => {
    const ui = buildBlueprintUiSections(NESTED_UNDER_SECTIONS, null);
    expect(ui.find((s) => s.title === 'SUMMARY').content).toContain('Outline complete');
    // The regression: SUMMARY et al. ended up inside DAY REFLECTION's content,
    // so regenerating Day Reflection would have deleted them.
    expect(ui.find((s) => s.title === 'SECTION OUTLINES').content).not.toContain('Outline complete');
  });

  it('still reshapes when the `## ` blocks all sit above the parts', () => {
    const ui = buildBlueprintUiSections(APPENDIX_ABOVE_PARTS, null);
    expect(ui.map((s) => s.title)).toEqual([
      'Overview', "Today's Mission", 'Learn It', 'Quick Check', 'Up Next in Class', 'Day Reflection',
    ]);
    expect(ui.every((s) => s.dlu)).toBe(true);
    // The appendix rides in Overview, which no part regeneration touches.
    expect(ui[0].content).toContain('ACS CODES ADDRESSED');
    expect(ui[0].content).toContain('None identified');
  });
});

describe('buildBlueprintUiSections — the shape flags the splice depends on', () => {
  it('marks DLU parts so the page splices instead of rebuilding as `## `', () => {
    expect(buildBlueprintUiSections(DLU, null).every((s) => s.dlu)).toBe(true);
  });
  it('leaves standard `## ` sections unflagged', () => {
    const ui = buildBlueprintUiSections(STD, null);
    expect(ui.some((s) => s.dlu)).toBe(false);
    expect(ui.some((s) => s.whole)).toBe(false);
  });
  it('marks the whole-document fallback as whole, never as DLU', () => {
    const ui = buildBlueprintUiSections('Prose with no structure at all.', null);
    expect(ui[0].whole).toBe(true);
    expect(ui[0].dlu).toBeUndefined();
  });
});
