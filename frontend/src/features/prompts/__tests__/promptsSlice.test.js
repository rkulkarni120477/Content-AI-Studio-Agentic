// @vitest-environment jsdom
//
// Tenant-isolation ticket: Style/CDD/Blueprint prompt dropdowns must never
// show another tenant's prompts. fetchPromptsThunk is re-dispatched with a
// new project_id whenever the selected tenant changes (InlinePromptControls),
// but a slower response for the PREVIOUS tenant landing after the new one
// used to blindly overwrite `prompts` — there is no client-side project_id
// filter to catch it (filteredPrompts only checks component_type). Fixed by
// the same requestId guard already used for clusters/courses in
// dashboardSlice.
import { describe, expect, it } from 'vitest';
import reducer from '../promptsSlice';
import { fetchPromptsThunk } from '../promptsThunks';

function pending(requestId) {
  return { type: fetchPromptsThunk.pending.type, meta: { requestId } };
}
function fulfilled(requestId, payload) {
  return { type: fetchPromptsThunk.fulfilled.type, meta: { requestId }, payload };
}

describe('promptsSlice — tenant-switch race', () => {
  it('ignores a stale fulfilled response from a superseded (previous-tenant) fetch', () => {
    let state = reducer(undefined, { type: '@@init' });

    // Tenant A's fetch starts.
    state = reducer(state, pending('req-A'));
    // User switches tenant before it resolves — tenant B's fetch starts.
    state = reducer(state, pending('req-B'));
    // Tenant B's (new tenant) response lands first.
    state = reducer(state, fulfilled('req-B', [{ id: 2, name: 'tenant-b-prompt' }]));
    expect(state.prompts).toEqual([{ id: 2, name: 'tenant-b-prompt' }]);

    // Tenant A's slower, superseded response finally lands — must not clobber.
    state = reducer(state, fulfilled('req-A', [{ id: 1, name: 'tenant-a-prompt' }]));
    expect(state.prompts).toEqual([{ id: 2, name: 'tenant-b-prompt' }]);
  });

  it('applies the response when it is the latest dispatched request', () => {
    let state = reducer(undefined, { type: '@@init' });
    state = reducer(state, pending('req-1'));
    state = reducer(state, fulfilled('req-1', [{ id: 1, name: 'prompt' }]));
    expect(state.prompts).toEqual([{ id: 1, name: 'prompt' }]);
  });
});
