/**
 * The block-wide payload must not quietly diverge from the single-document one.
 *
 * This is a source-level guard rather than a render test, and deliberately so: the
 * bug it exists to prevent is not a broken component but two payload builders on the
 * same form drifting apart. `onGenerate` sent `style_id`; `onGenerateBlock`, added
 * later beside it, did not — so the page kept rendering "<style> will be applied"
 * over a block-wide generation that ignored the style entirely, and the server had
 * no way to tell "no style selected" from "style never sent". Audited 2026-08-13.
 *
 * Rendering these 900-line pages to assert one field would cost far more than it
 * protects; what matters is that whoever edits one builder is failed by the other.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

const read = (p) => readFileSync(resolve(__dirname, '../../..', p), 'utf8');

/** The body of a named async function, up to its closing brace at column 0-ish. */
function functionBody(source, name) {
  const start = source.indexOf(`async function ${name}(`);
  expect(start, `${name} not found`).toBeGreaterThan(-1);
  const end = source.indexOf('\n  }', start);
  expect(end, `${name} has no recognisable end`).toBeGreaterThan(start);
  return source.slice(start, end);
}

describe('CDD page', () => {
  const source = read('features/cdd/pages/CddPage/CddPage.jsx');

  it('sends the selected style with a block-wide generation', () => {
    expect(functionBody(source, 'onGenerateBlock')).toContain('style_id');
  });

  it('still sends it with a single-document generation', () => {
    expect(functionBody(source, 'onGenerate')).toContain('style_id');
  });

  it('does not send a document subset, which has no meaning for a whole block', () => {
    // Also the negative control for functionBody above: reference_document_ids IS in
    // onGenerate, so if this slice were over-wide the assertion would fail and every
    // other assertion in this file would be worthless.
    expect(functionBody(source, 'onGenerate')).toContain('reference_document_ids');
    expect(functionBody(source, 'onGenerateBlock')).not.toContain('reference_document_ids');
  });

  it.each(['extra_instructions', 'estimated_duration_hours', 'prompt_id'])(
    'sends %s with a block-wide generation',
    (field) => {
      // Each of these is a control the user can see and set. All three reach a model
      // server-side now (see promptops_app/services/user_directives.py), so dropping
      // one here would silently disable it again.
      expect(functionBody(source, 'onGenerateBlock')).toContain(field);
    },
  );
});

describe('Blueprint page', () => {
  const source = read('features/blueprint/pages/BlueprintPage/BlueprintPage.jsx');

  it('sends the active style with a per-module generation', () => {
    expect(functionBody(source, 'onGenerate')).toContain('style_id');
  });

  // The block-wide (digest) flow is deliberately not offered on this page — the
  // panel, its payload builder and its job-resume effect were removed together, so
  // a half-restored version (a builder with no panel, or a panel with no style)
  // fails here rather than shipping.
  it('offers no block-wide flow at all', () => {
    expect(source).not.toContain('BlockWidePanel');
    expect(source).not.toContain('onGenerateBlock');
  });
});
