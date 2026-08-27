/**
 * The block-wide payload must not quietly diverge from the single-document one.
 *
 * The source-level half below is a guard rather than a render test, and deliberately
 * so: the bug it exists to prevent is not a broken component but two payload builders
 * on the same form drifting apart. `onGenerate` sent `style_id`; `onGenerateBlock`,
 * added later beside it, did not — so the page kept rendering "<style> will be applied"
 * over a block-wide generation that ignored the style entirely, and the server had
 * no way to tell "no style selected" from "style never sent". Audited 2026-08-13.
 *
 * Rendering these 900-line pages to assert one field would cost far more than it
 * protects; what matters is that whoever edits one builder is failed by the other.
 *
 * WHY THERE IS ALSO A BEHAVIOURAL HALF (added 2026-08-27)
 * ------------------------------------------------------
 * Grepping the page proved the page's half and nothing else, and the page was never
 * the broken half. `cddService.mapBlockPayload` is a whitelist between the page and
 * the HTTP call, and it did not list style_id or prompt_id — so both were built by
 * the page, asserted present by the tests above, and then dropped before the request.
 * Production consequence: 30/30 block-wide CDD jobs reached the server with no
 * prompt_id, so prompt_guidance never reached its DB tier and distilled every day's
 * MAP guidance from the shipped cdd_generation.md file (a middle-school CTE template)
 * regardless of which prompt the user selected. The tests below assert what actually
 * goes over the wire, which is the only thing the server can act on.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@services/apiClient', () => ({
  api: { post: vi.fn(() => Promise.resolve({ data: {} })), get: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { api } = await import('@services/apiClient');
const { cddService } = await import('@features/cdd/services/cddService');
const { blueprintService } = await import('@features/blueprint/services/blueprintService');

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

describe('what the block-wide request actually carries', () => {
  beforeEach(() => { api.post.mockClear(); });

  /** The body handed to api.post by the last call. */
  const sentBody = () => api.post.mock.calls[0][1];

  const formValues = {
    block: 'Block 9',
    course_id: 101,
    project_id: 23,
    course_title: 'Block 9 Aircraft Systems-II',
    quality_tier: 'standard',
    model_choice: 'GPT-5.4',
    style_id: 7,
    prompt_id: 82,
  };

  it('carries the selected prompt to the server', async () => {
    await cddService.generateCddBlock(formValues);
    // The whole point: without this the server cannot resolve the user's choice at
    // all on this path, and falls through to the shipped file template.
    expect(sentBody().prompt_id).toBe(82);
  });

  it('carries the selected style to the server', async () => {
    await cddService.generateCddBlock(formValues);
    expect(sentBody().style_id).toBe(7);
  });

  it('distinguishes "no style chosen" from "style never sent"', async () => {
    await cddService.generateCddBlock({ ...formValues, style_id: undefined });
    // null, not undefined — undefined is dropped by JSON.stringify, which is exactly
    // the ambiguity the server was left with before.
    expect(sentBody()).toHaveProperty('style_id', null);
  });

  it('omits an unselected prompt rather than sending a null the server must special-case', async () => {
    await cddService.generateCddBlock({ ...formValues, prompt_id: undefined });
    expect(sentBody().prompt_id).toBeUndefined();
  });

  it('carries both on the Blueprint twin as well', async () => {
    await blueprintService.generateBlueprintBlock(formValues);
    expect(sentBody().prompt_id).toBe(82);
    expect(sentBody().style_id).toBe(7);
  });

  it('still narrows the payload — reference docs have no meaning for a whole block', async () => {
    // Negative control. If the mapper ever became a pass-through, every assertion
    // above would pass for the wrong reason.
    await cddService.generateCddBlock({ ...formValues, reference_document_ids: [1, 2] });
    expect(sentBody().reference_document_ids).toBeUndefined();
  });
});
