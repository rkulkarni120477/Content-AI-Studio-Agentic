import { describe, expect, it } from 'vitest';
import {
  detectDluBlueprint,
  parseDluBlueprintSections,
  replaceDluBlueprintSection,
} from '@utils/dluBlueprint';
import { buildBlueprintUiSections } from '@utils/blueprintContent';

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
