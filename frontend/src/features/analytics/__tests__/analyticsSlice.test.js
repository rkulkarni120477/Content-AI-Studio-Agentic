// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';

import reducer from '@features/analytics/analyticsSlice';
import { fetchSummaryThunk, fetchProjectAnalyticsThunk } from '@features/analytics/analyticsThunks';

// CAS-18 / CAS-137: switching the date-range dropdown (Last 7 Days -> Last 30
// Days) fired a new fetchSummaryThunk while the previous one could still be
// in flight. Both requests resolve to the SAME reducer case with no way to
// tell them apart, so whichever response landed last won -- not whichever
// one matched the currently-selected range. A user could see "Last 30 Days"
// numbers snap back to the "Last 7 Days" ones a moment later. Same
// requestId guard as dashboardSlice's coursesRequestId/clustersRequestId.
function reduce(actions) {
  const store = configureStore({ reducer: { analytics: reducer } });
  actions.forEach((a) => store.dispatch(a));
  return store.getState().analytics;
}

describe('analyticsSlice — summary/project fetches guard against out-of-order responses', () => {
  it('a superseded summary response cannot overwrite the latest one', () => {
    const state = reduce([
      fetchSummaryThunk.pending('request-7-days', { dateRange: 'last_7_days' }),
      fetchSummaryThunk.pending('request-30-days', { dateRange: 'last_30_days' }),
      // The 7-day request finally resolves AFTER the 30-day one was dispatched.
      fetchSummaryThunk.fulfilled({ generations: 786 }, 'request-7-days', { dateRange: 'last_7_days' }),
    ]);
    expect(state.summary).toBeNull();
    expect(state.isLoading).toBe(true);
  });

  it('the latest summary response is applied', () => {
    const state = reduce([
      fetchSummaryThunk.pending('request-7-days', { dateRange: 'last_7_days' }),
      fetchSummaryThunk.pending('request-30-days', { dateRange: 'last_30_days' }),
      fetchSummaryThunk.fulfilled({ generations: 956 }, 'request-30-days', { dateRange: 'last_30_days' }),
    ]);
    expect(state.summary).toEqual({ generations: 956 });
    expect(state.isLoading).toBe(false);
  });

  it('the same race guard applies to the Project-Level Comparison table', () => {
    const state = reduce([
      fetchProjectAnalyticsThunk.pending('request-7-days', {}),
      fetchProjectAnalyticsThunk.pending('request-30-days', {}),
      fetchProjectAnalyticsThunk.fulfilled([{ project_id: 1, generations: 165 }], 'request-7-days', {}),
    ]);
    expect(state.projectRows).toEqual([]);
  });
});
