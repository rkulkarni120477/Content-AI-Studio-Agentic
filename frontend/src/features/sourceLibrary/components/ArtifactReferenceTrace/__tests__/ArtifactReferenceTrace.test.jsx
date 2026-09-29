// @vitest-environment jsdom
/** CAS AIM findings, Phase 3: EditorPage now passes Generation.generation_params
 * (a JSON string, {"dis_source_units": [...]}) straight from the API into this
 * component's generationParams prop. Pin the exact contract so a backend field
 * rename doesn't silently make the panel show nothing. */
import { describe, expect, it } from 'vitest';
import { extractArtifactReferences } from '../ArtifactReferenceTrace';

describe('extractArtifactReferences — Generation.generation_params contract', () => {
  it('reads dis_source_units out of the raw JSON string the API returns', () => {
    const generationParams = JSON.stringify({
      dis_source_units: [
        { content_unit_id: 'u1', title: 'FAA Handbook p.12', unit_type: 'page', job_id: 'faa' },
        { content_unit_id: 'u2', title: 'Hydraulics Textbook p.4', unit_type: 'page', job_id: 'book_a' },
      ],
    });

    const refs = extractArtifactReferences(generationParams);

    expect(refs).toHaveLength(2);
    expect(refs.map((r) => r.title)).toEqual(['FAA Handbook p.12', 'Hydraulics Textbook p.4']);
  });

  it('is empty (not an error) when the generation predates this field', () => {
    expect(extractArtifactReferences(undefined, null)).toEqual([]);
  });

  it('is empty when dis_source_units is present but empty (no DIS context resolved)', () => {
    const generationParams = JSON.stringify({ dis_source_units: [] });
    expect(extractArtifactReferences(generationParams)).toEqual([]);
  });
});
