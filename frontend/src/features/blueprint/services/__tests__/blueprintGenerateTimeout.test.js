/**
 * Outline (Blueprint) generation ticket, Issue 1: the backend runs Blueprint
 * generation synchronously in the request/response cycle (DIS retrieval + LLM
 * call + parsing + save, no job_id, no polling). generateBlueprint() used to
 * call api.post() with no timeout override, so it inherited apiClient's flat
 * 120s default -- a large-output model or the backend's own retry/fallback
 * cascade can run past that, aborting the request client-side with no
 * response while the backend keeps working and saves the Blueprint anyway.
 * The user saw a false "Something went wrong" until they refreshed.
 *
 * Fixed by giving this call the same generous-timeout treatment uploads
 * already get, instead of the app-wide default.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@services/apiClient', () => ({
  api: {
    post: vi.fn().mockResolvedValue({ blueprint_id: null }),
    get: vi.fn().mockResolvedValue({}),
  },
}));

import { api } from '@services/apiClient';
import { blueprintService } from '../blueprintService';

describe('blueprintService.generateBlueprint timeout', () => {
  beforeEach(() => vi.clearAllMocks());

  it('overrides the app-wide 120s default with a generous bounded timeout', async () => {
    await blueprintService.generateBlueprint({ course_id: 1, project_id: 1, selected_module: 1 });

    const [, , config] = api.post.mock.calls[0];
    expect(config?.timeout).toBeGreaterThan(120_000);
  });
});
