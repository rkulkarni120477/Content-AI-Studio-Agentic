// @vitest-environment jsdom
//
// A Day 9 outline written entirely in `### ` headings (0 × `## `) used to reach
// this view as ONE whole-document section: 250 lines with a single Save and a
// single Regenerate that rewrote everything. These tests pin that it now renders
// a section per heading, that each one hands the page the line range it must
// splice, and that two sections sharing a title stay independent — the case
// where keying UI state on the title would have merged them into one editor.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

vi.mock('@hooks/useLabels', () => ({
  useLabels: () => ({ blueprint: 'Blueprint' }),
}));

const BlueprintContentView = (
  await import('../BlueprintContentView')
).default;

afterEach(cleanup);

const H3_DOC = [
  '### Part I — DLU-Wide Information',
  '',
  '- **Block / Day:** Block 2 / Day 9',
  '',
  "### Screen: Today's Mission",
  '',
  'Frame the day.',
  '',
  '### Screen: Quick Check',
  '',
  '- Item one',
  '- Item two',
].join('\n');

const DUPLICATES = [
  '### Screen: Quick Check',
  '',
  'FIRST BODY',
  '',
  '### Screen: Quick Check',
  '',
  'SECOND BODY',
].join('\n');

const NO_HEADINGS = "I'm unable to create a blueprint because the sources are missing.";

let onSaveSection;
let onRegenerateSection;

beforeEach(() => {
  onSaveSection = vi.fn().mockResolvedValue(undefined);
  onRegenerateSection = vi.fn().mockResolvedValue(undefined);
});

function renderView(fullContent, props = {}) {
  return render(
    <BlueprintContentView
      fullContent={fullContent}
      sections={null}
      editable
      onSaveSection={onSaveSection}
      onRegenerateSection={onRegenerateSection}
      {...props}
    />,
  );
}

// This project has no jest-dom, so assertions are plain values and DOM lookups
// use the query* (non-throwing) variants where absence is a valid outcome.

/** The accordion toggles, in document order. Only these carry aria-expanded. */
function sectionHeads() {
  return screen.getAllByRole('button')
    .filter((b) => b.getAttribute('aria-expanded') !== null);
}

function openSection(name, nth = 0) {
  const hits = sectionHeads().filter((b) => b.textContent.includes(name));
  expect(hits.length, `expected a section head containing "${name}"`).toBeGreaterThan(nth);
  fireEvent.click(hits[nth]);
}

describe('a `### `-sectioned document gets one editor per heading', () => {
  it('renders a section head per heading, not one whole-document block', () => {
    renderView(H3_DOC);
    const titles = sectionHeads().map((b) => b.textContent.replace(/[▸▾]/g, '').trim());
    expect(titles).toEqual([
      'Part I — DLU-Wide Information',
      "Screen: Today's Mission",
      'Screen: Quick Check',
    ]);
    expect(screen.queryByText(/Full Document/)).toBeNull();
  });

  it('shows no structure notice — nothing about this document is degraded', () => {
    renderView(H3_DOC);
    expect(screen.queryByText(/edited as a single document/i)).toBeNull();
    expect(screen.queryByText(/wasn't recognised/i)).toBeNull();
  });

  it('loads the section body verbatim into its own textarea', () => {
    renderView(H3_DOC);
    openSection('Screen: Quick Check');
    expect(screen.getByLabelText(/Edit this section/).value).toBe('- Item one\n- Item two');
  });

  it('hands the page the line range to splice, not just a title', () => {
    renderView(H3_DOC);
    openSection('Screen: Quick Check');
    fireEvent.change(screen.getByLabelText(/Edit this section/), { target: { value: 'NEW' } });
    fireEvent.change(screen.getByLabelText(/Edit reason/), { target: { value: 'because' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Edit/ }));

    expect(onSaveSection).toHaveBeenCalledTimes(1);
    const arg = onSaveSection.mock.calls[0][0];
    expect(arg.sectionTitle).toBe('Screen: Quick Check');
    expect(arg.content).toBe('NEW');
    expect(arg.reason).toBe('because');
    expect(arg.isWholeDocument).toBe(false);
    expect(arg.isDluSection).toBe(false);
    // Lines 8..12 of H3_DOC — the heading line and everything under it.
    expect(arg.headingLocator).toMatchObject({
      level: 3,
      startLine: 8,
      headingText: '### Screen: Quick Check',
      endHeadingText: null,
    });
  });

  it('offers per-section regeneration without demanding an instruction first', () => {
    // Only the whole-document button is gated on an instruction, because that
    // one discards the entire document.
    renderView(H3_DOC);
    openSection('Screen: Quick Check');
    const regen = screen.getByRole('button', { name: /Regenerate Section/ });
    expect(regen.disabled).toBe(false);
    fireEvent.click(regen);
    expect(onRegenerateSection).toHaveBeenCalledTimes(1);
    expect(onRegenerateSection.mock.calls[0][0].headingLocator.startLine).toBe(8);
  });
});

describe('two sections sharing a title stay independent', () => {
  it('opens only the one that was clicked', () => {
    renderView(DUPLICATES);
    openSection('Screen: Quick Check', 1);
    const editor = screen.getByLabelText(/Edit this section/);
    expect(editor.value).toBe('SECOND BODY');
  });

  it('keeps their drafts apart', () => {
    renderView(DUPLICATES);
    openSection('Screen: Quick Check', 1);
    fireEvent.change(screen.getByLabelText(/Edit this section/), { target: { value: 'EDITED 2' } });
    // Collapse it, open the first, and the first must still hold its own text.
    openSection('Screen: Quick Check', 1);
    openSection('Screen: Quick Check', 0);
    expect(screen.getByLabelText(/Edit this section/).value).toBe('FIRST BODY');
  });

  it('sends the clicked one’s range, so the other is never overwritten', () => {
    renderView(DUPLICATES);
    openSection('Screen: Quick Check', 1);
    fireEvent.change(screen.getByLabelText(/Edit reason/), { target: { value: 'r' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Edit/ }));
    expect(onSaveSection.mock.calls[0][0].headingLocator.startLine).toBe(4);
  });
});

describe('a document with no headings at all', () => {
  it('still gets one editable whole-document section', () => {
    renderView(NO_HEADINGS);
    expect(screen.getByLabelText(/Edit this document/).value).toBe(NO_HEADINGS);
    expect(screen.queryByRole('button', { name: /Regenerate Whole Document/ })).not.toBeNull();
  });

  it('says what the mode is without blaming the document', () => {
    renderView(NO_HEADINGS);
    expect(screen.queryByText(/no section headings/i)).not.toBeNull();
    expect(screen.queryByText(/edited as a single document/i)).not.toBeNull();
    // The old copy read as "your blueprint is malformed" and named a cause the
    // reader could do nothing about.
    expect(screen.queryByText(/wasn't recognised/i)).toBeNull();
    expect(screen.queryByText(/structure/i)).toBeNull();
  });

  it('marks itself whole rather than handing over a line range', () => {
    renderView(NO_HEADINGS);
    fireEvent.change(screen.getByLabelText(/Edit reason/), { target: { value: 'r' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Edit/ }));
    const arg = onSaveSection.mock.calls[0][0];
    expect(arg.isWholeDocument).toBe(true);
    expect(arg.headingLocator).toBeUndefined();
  });
});

describe('regeneration is grounded in the text on screen', () => {
  // The prompt is built from the section title, the module name and a 1500-char
  // CDD summary. Without the current text the model cannot revise anything — it
  // drafts a replacement it has never seen, which is how a Day 20 exam blueprint
  // came back as generic "Lesson 1/2/3" filler (blueprint_versions 396 -> 397).
  it('sends the section body when regenerating a section', () => {
    renderView(H3_DOC);
    openSection('Screen: Quick Check');
    fireEvent.click(screen.getByRole('button', { name: /Regenerate Section/ }));
    expect(onRegenerateSection.mock.calls[0][0].sectionContent).toBe('- Item one\n- Item two');
  });

  it('sends the whole document when regenerating a whole-document section', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    try {
      renderView(NO_HEADINGS);
      fireEvent.change(screen.getByLabelText(/Edit reason/), { target: { value: 'fix it' } });
      fireEvent.click(screen.getByRole('button', { name: /Regenerate Whole Document/ }));
      const arg = onRegenerateSection.mock.calls[0][0];
      expect(arg.sectionContent).toBe(NO_HEADINGS);
      expect(arg.isWholeDocument).toBe(true);
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it('no longer promises that the current text is withheld', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    try {
      renderView(NO_HEADINGS);
      fireEvent.change(screen.getByLabelText(/Edit reason/), { target: { value: 'fix it' } });
      fireEvent.click(screen.getByRole('button', { name: /Regenerate Whole Document/ }));
      const shown = confirmSpy.mock.calls[0][0];
      expect(shown).toMatch(/given the current text/i);
      expect(shown).not.toMatch(/not sent to the model/i);
      // Cancelling must spend nothing.
      expect(onRegenerateSection).not.toHaveBeenCalled();
    } finally {
      confirmSpy.mockRestore();
    }
  });
});
