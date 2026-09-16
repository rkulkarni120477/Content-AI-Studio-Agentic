/**
 * Blueprint generate is now async (202 JobAccepted). The former 600s client
 * timeout override is no longer needed — the POST returns a job handle
 * immediately and JobTracker polls status.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@services/apiClient', () => ({
  api: {
    post: vi.fn().mockResolvedValue({ job_id: 'j1', status: 'queued' }),
    get: vi.fn().mockResolvedValue({}),
  },
}));

import { api } from '@services/apiClient';
import { blueprintService } from '../blueprintService';

describe('blueprintService.generateBlueprint', () => {
  beforeEach(() => vi.clearAllMocks());

  it('posts generate and returns the job-accepted payload (no long timeout)', async () => {
    const res = await blueprintService.generateBlueprint({
      course_id: 1, project_id: 1, selected_module: 1,
    });

    expect(api.post).toHaveBeenCalledTimes(1);
    const [, body, config] = api.post.mock.calls[0];
    expect(body.course_id).toBe(1);
    // Default axios timeout is fine — this is a 202 enqueue, not a sync LLM call.
    expect(config?.timeout).toBeUndefined();
    expect(res).toEqual({ job_id: 'j1', status: 'queued' });
  });
});
