/**
 * G1 (AIM_PIPELINE_REPAIR_WORKFLOW.txt, step 6): mapLaunchPayload was an
 * explicit key whitelist that did not include `block` or `day` -- so even a
 * caller that passed them had them stripped before the request left the
 * browser, and the server's day-scoped structured retrieval (already built
 * and already tested) never fired for a single generation launched from the
 * product.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@services/apiClient', () => ({
  api: { post: vi.fn().mockResolvedValue({ job_id: 'job-1' }) },
}));

import { api } from '@services/apiClient';
import { generateService } from '../services/generateService';

describe('generateService.launch payload', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sends day through to the request body', async () => {
    await generateService.launch({
      course_id: 1, project_id: 1,
      component_value: 'lesson_4', component_label: 'Day 4', component_type: 'lesson',
      day: 4,
    });

    const [, body] = api.post.mock.calls[0];
    expect(body.day).toBe(4);
  });

  it('sends an explicit block through when the caller has one', async () => {
    await generateService.launch({
      course_id: 1, project_id: 1,
      component_value: 'lesson_4', component_label: 'Day 4', component_type: 'lesson',
      day: 4, block: 'Block 9',
    });

    const [, body] = api.post.mock.calls[0];
    expect(body.block).toBe('Block 9');
  });

  it('omits block when the caller has none, so the server derives it', async () => {
    await generateService.launch({
      course_id: 1, project_id: 1,
      component_value: 'lesson_4', component_label: 'Day 4', component_type: 'lesson',
      day: 4,
    });

    const [, body] = api.post.mock.calls[0];
    expect(body.block).toBeUndefined();
  });

  it('omits day when the caller has none, at all -- not null, not 0', async () => {
    await generateService.launch({
      course_id: 1, project_id: 1,
      component_value: 'lesson_1', component_label: 'Lesson 1', component_type: 'lesson',
    });

    const [, body] = api.post.mock.calls[0];
    expect(body.day).toBeUndefined();
  });
});
