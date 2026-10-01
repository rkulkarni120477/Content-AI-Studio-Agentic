import { createSlice } from '@reduxjs/toolkit';
import {
  fetchActiveChecklistThunk,
  uploadChecklistThunk,
  updateChecklistItemThunk,
  deleteChecklistItemsThunk,
  deleteChecklistThunk,
  fetchLatestReviewThunk,
  fetchReviewHistoryThunk,
  startReviewThunk,
  fetchChecklistResultsThunk,
  fetchFindingsThunk,
  applyFindingThunk,
  dismissFindingThunk,
} from './reviewThunks';

const initialState = {
  checklist:    null,     // active checklist { id, name, version, items: [...] } or null
  loaded:       false,    // whether we've fetched at least once (null = genuinely none)
  isLoading:    false,
  isUploading:  false,
  savingItemId: null,     // rule id currently being saved
  // ── Review run ──
  review:          null,  // latest ContentReview for the selected generation
  checklistResults: [],   // non-pass rule outcomes for the current review
  findings:        [],    // detected issues for the current review
  history:         [],    // past review runs for the selected generation
  isRunning:       false,
  reused:          false, // last start returned an identical prior run
  error:           null,
};

/** Replace one finding in-place by id. */
function replaceFinding(list, updated) {
  if (!updated?.id) return list;
  return list.map((f) => (f.id === updated.id ? updated : f));
}

/** Replace one rule in-place by id within the active checklist. */
function replaceItem(checklist, updated) {
  if (!checklist?.items) return checklist;
  return {
    ...checklist,
    items: checklist.items.map((i) => (i.id === updated.id ? updated : i)),
  };
}

const reviewSlice = createSlice({
  name: 'review',
  initialState,
  reducers: {
    clearReviewError(s) { s.error = null; },
    // Clear run state when the selected generation changes.
    resetReviewRun(s) {
      s.review = null; s.reused = false; s.isRunning = false;
      s.checklistResults = []; s.findings = [];
    },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchActiveChecklistThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchActiveChecklistThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.loaded = true;
        s.checklist = payload || null;
      })
      .addCase(fetchActiveChecklistThunk.rejected,  (s, { payload }) => {
        s.isLoading = false; s.loaded = true; s.error = payload;
      })

      .addCase(uploadChecklistThunk.pending,   (s) => { s.isUploading = true; s.error = null; })
      .addCase(uploadChecklistThunk.fulfilled, (s, { payload }) => {
        s.isUploading = false;
        s.loaded = true;
        if (payload) s.checklist = payload;
      })
      .addCase(uploadChecklistThunk.rejected,  (s, { payload }) => { s.isUploading = false; s.error = payload; })

      .addCase(updateChecklistItemThunk.pending, (s, { meta }) => {
        s.savingItemId = meta.arg?.itemId ?? null; s.error = null;
      })
      .addCase(updateChecklistItemThunk.fulfilled, (s, { payload }) => {
        s.savingItemId = null;
        if (payload?.id) s.checklist = replaceItem(s.checklist, payload);
      })
      .addCase(updateChecklistItemThunk.rejected, (s, { payload }) => {
        s.savingItemId = null; s.error = payload;
      })

      .addCase(deleteChecklistItemsThunk.fulfilled, (s, { payload }) => {
        const gone = new Set(payload.itemIds);
        if (s.checklist?.items) {
          s.checklist = { ...s.checklist, items: s.checklist.items.filter((i) => !gone.has(i.id)) };
        }
      })
      .addCase(deleteChecklistItemsThunk.rejected, (s, { payload }) => { s.error = payload; })

      .addCase(deleteChecklistThunk.fulfilled, (s) => { s.checklist = null; s.loaded = true; })
      .addCase(deleteChecklistThunk.rejected, (s, { payload }) => { s.error = payload; })

      .addCase(fetchLatestReviewThunk.fulfilled, (s, { payload }) => { s.review = payload || null; })

      .addCase(startReviewThunk.pending,   (s) => { s.isRunning = true; s.reused = false; s.error = null; })
      .addCase(startReviewThunk.fulfilled, (s, { payload }) => {
        s.isRunning = false;
        s.reused = Boolean(payload?.reused);
        if (payload?.review) s.review = payload.review;
      })
      .addCase(startReviewThunk.rejected,  (s, { payload }) => { s.isRunning = false; s.error = payload; })

      .addCase(fetchChecklistResultsThunk.fulfilled, (s, { payload }) => { s.checklistResults = payload || []; })

      .addCase(fetchFindingsThunk.fulfilled, (s, { payload }) => { s.findings = payload || []; })
      .addCase(fetchReviewHistoryThunk.fulfilled, (s, { payload }) => { s.history = payload || []; })

      // Apply/dismiss return the updated finding — swap it in place.
      .addCase(applyFindingThunk.fulfilled,   (s, { payload }) => { s.findings = replaceFinding(s.findings, payload); })
      .addCase(applyFindingThunk.rejected,    (s, { payload }) => { s.error = payload; })
      .addCase(dismissFindingThunk.fulfilled, (s, { payload }) => { s.findings = replaceFinding(s.findings, payload); })
      .addCase(dismissFindingThunk.rejected,  (s, { payload }) => { s.error = payload; });
  },
});

export const { clearReviewError, resetReviewRun } = reviewSlice.actions;

export const selectReviewChecklist   = (s) => s.review.checklist;
export const selectReviewLoaded      = (s) => s.review.loaded;
export const selectReviewLoading     = (s) => s.review.isLoading;
export const selectReviewUploading   = (s) => s.review.isUploading;
export const selectReviewSavingItem  = (s) => s.review.savingItemId;
export const selectReviewRun         = (s) => s.review.review;
export const selectReviewRunning     = (s) => s.review.isRunning;
export const selectChecklistResults  = (s) => s.review.checklistResults;
export const selectFindings          = (s) => s.review.findings;
export const selectReviewHistory     = (s) => s.review.history;
export const selectReviewError       = (s) => s.review.error;

export default reviewSlice.reducer;
