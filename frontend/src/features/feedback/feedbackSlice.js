import { createSlice } from '@reduxjs/toolkit';
import {
  fetchFeedbackThunk, analyzeFeedbackThunk,
  deleteFeedbackItemThunk, bulkDeleteFeedbackThunk,
} from './feedbackThunks';

const initialState = {
  items:        [],
  isLoading:    false,
  isProcessing: false,   // AI analysis of an upload in flight
  error:        null,
};

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
export const selectFeedbackError      = (s) => s.feedback.error;
