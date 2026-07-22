import { createSlice } from '@reduxjs/toolkit';
import {
  fetchFeedbackThunk, analyzeFeedbackThunk, recommendFeedbackThunk,
  deleteFeedbackItemThunk, bulkDeleteFeedbackThunk,
} from './feedbackThunks';

const initialState = {
  items:           [],
  isLoading:       false,
  isProcessing:    false,   // AI analysis of an upload in flight
  recommendingIds: [],      // feedback item ids with a recommendation in flight
  error:           null,
};

/** Replace items in-place by id with the server's updated copies. */
function mergeItems(list, updated) {
  const byId = new Map(updated.map((u) => [u.id, u]));
  return list.map((i) => byId.get(i.id) || i);
}

const feedbackSlice = createSlice({
  name: 'feedback',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchFeedbackThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchFeedbackThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.items = payload ?? [];
      })
      .addCase(fetchFeedbackThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(analyzeFeedbackThunk.pending,   (s) => { s.isProcessing = true; s.error = null; })
      .addCase(analyzeFeedbackThunk.fulfilled, (s, { payload }) => {
        s.isProcessing = false;
        // Prepend the newly-extracted items so they appear at the top.
        s.items = [...(payload?.items ?? []), ...s.items];
      })
      .addCase(analyzeFeedbackThunk.rejected,  (s, { payload }) => { s.isProcessing = false; s.error = payload; })

      .addCase(recommendFeedbackThunk.pending, (s, { meta }) => {
        s.error = null;
        const ids = (meta.arg?.itemIds || []).map(Number);
        s.recommendingIds = [...new Set([...s.recommendingIds, ...ids])];
      })
      .addCase(recommendFeedbackThunk.fulfilled, (s, { payload }) => {
        const done = new Set((payload.requestedIds || []).map(Number));
        s.recommendingIds = s.recommendingIds.filter((id) => !done.has(id));
        s.items = mergeItems(s.items, payload.items || []);
      })
      .addCase(recommendFeedbackThunk.rejected, (s, { payload, meta }) => {
        const done = new Set((meta.arg?.itemIds || []).map(Number));
        s.recommendingIds = s.recommendingIds.filter((id) => !done.has(id));
        s.error = payload;
      })

      .addCase(deleteFeedbackItemThunk.fulfilled, (s, { payload }) => {
        s.items = s.items.filter((i) => i.id !== payload.id);
      })

      .addCase(bulkDeleteFeedbackThunk.fulfilled, (s, { payload }) => {
        const removed = new Set(payload.ids);
        s.items = s.items.filter((i) => !removed.has(i.id));
      });
  },
});

export const { clearError } = feedbackSlice.actions;
export default feedbackSlice.reducer;

export const selectFeedbackItems      = (s) => s.feedback.items;
export const selectFeedbackLoading    = (s) => s.feedback.isLoading;
export const selectFeedbackProcessing = (s) => s.feedback.isProcessing;
export const selectFeedbackRecommending = (s) => s.feedback.recommendingIds;
export const selectFeedbackError      = (s) => s.feedback.error;
