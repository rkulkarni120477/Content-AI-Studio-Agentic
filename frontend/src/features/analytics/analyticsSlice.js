import { createSlice } from '@reduxjs/toolkit';
import {
  fetchSummaryThunk,
  fetchFeedbackThunk,
  fetchProjectAnalyticsThunk,
  fetchGenerationHistoryThunk,
  fetchHistoryExtrasThunk, fetchFeedbackSummaryThunk,
  fetchReviewsThunk, fetchLlmCostThunk,
  fetchPermissionsOverviewThunk, fetchClearPresetsThunk,
  fetchBudgetsThunk, upsertBudgetThunk, deleteBudgetThunk,
  fetchGenerationTraceThunk,
} from './analyticsThunks';

const initialState = {
  summary:    null,
  projectRows: [],
  genHistory: [],
  promptVersionHistory: [],
  docUploadHistory: [],
  cddBpHistory: [],
  historyExtrasError: null,
  feedbackSummary: { total: 0, learning: 0, one_time: 0 },
  feedback:   { items: [], total: 0 },
  feedbackScope: 'learning',
  reviews:    { total: 0, approved: 0, avg_score: 0, items: [] },
  llmCost:    null,
  permissionsOverview: null,
  clearPresets: [],
  budgets: [],
  budgetsError: null,
  generationTrace: null,
  generationTraceLoading: false,
  generationTraceError: null,
  filters: {
    dateRange:  'last_30_days',
    page:       1,
    pageSize:   50,
  },
  isLoading:  false,
  error:      null,
  // Tracks the requestId of the most recently DISPATCHED summary/project
  // fetch, so a slower/out-of-order response from an earlier, superseded
  // fetch (the "Last 7 Days" request still in flight when the user has
  // already switched to "Last 30 Days", whose own fetch resolves first)
  // can't win the race and clobber the dashboard with the wrong range's
  // numbers once it finally lands. Same pattern as dashboardSlice's
  // coursesRequestId/clustersRequestId.
  summaryRequestId: null,
  projectRowsRequestId: null,
};

const analyticsSlice = createSlice({
  name: 'analytics',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    setFilters(s, { payload }) { s.filters = { ...s.filters, ...payload }; },
    setFeedbackScope(s, { payload }) { s.feedbackScope = payload; },
    resetFilters(s) { s.filters = initialState.filters; },
    clearGenerationTrace(s) { s.generationTrace = null; s.generationTraceError = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchSummaryThunk.pending,   (s, action) => {
        s.summaryRequestId = action.meta.requestId;
        s.isLoading = true;
      })
      .addCase(fetchSummaryThunk.fulfilled,  (s, action) => {
        if (action.meta.requestId !== s.summaryRequestId) return;
        s.isLoading = false;
        s.summary = action.payload;
      })
      .addCase(fetchSummaryThunk.rejected,   (s, action) => {
        if (action.meta.requestId !== s.summaryRequestId) return;
        s.isLoading = false;
        s.error = action.payload;
      })

      .addCase(fetchProjectAnalyticsThunk.pending, (s, action) => {
        s.projectRowsRequestId = action.meta.requestId;
      })
      .addCase(fetchProjectAnalyticsThunk.fulfilled, (s, action) => {
        if (action.meta.requestId !== s.projectRowsRequestId) return;
        s.projectRows = action.payload || [];
      })
      .addCase(fetchGenerationHistoryThunk.fulfilled, (s, { payload }) => { s.genHistory = payload || []; })
      .addCase(fetchHistoryExtrasThunk.pending, (s) => { s.historyExtrasError = null; })
      .addCase(fetchHistoryExtrasThunk.fulfilled, (s, { payload }) => {
        s.promptVersionHistory = payload?.promptVersions ?? [];
        s.docUploadHistory = payload?.docUploads ?? [];
        s.cddBpHistory = payload?.cddBpEvents ?? [];
        s.historyExtrasError = payload?.errors?.length ? payload.errors.join('; ') : null;
      })
      .addCase(fetchHistoryExtrasThunk.rejected, (s, { payload }) => {
        s.historyExtrasError = payload || 'Failed to load system history';
      })

      .addCase(fetchFeedbackSummaryThunk.fulfilled, (s, { payload }) => { s.feedbackSummary = payload || s.feedbackSummary; })
      .addCase(fetchFeedbackThunk.fulfilled, (s, { payload }) => { s.feedback = payload; })
      .addCase(fetchReviewsThunk.fulfilled, (s, { payload }) => { s.reviews = payload || s.reviews; })
      .addCase(fetchLlmCostThunk.fulfilled, (s, { payload }) => { s.llmCost = payload; })

      .addCase(fetchPermissionsOverviewThunk.fulfilled, (s, { payload }) => { s.permissionsOverview = payload; })
      .addCase(fetchClearPresetsThunk.fulfilled, (s, { payload }) => { s.clearPresets = payload || []; })

      .addCase(fetchBudgetsThunk.pending, (s) => { s.budgetsError = null; })
      .addCase(fetchBudgetsThunk.fulfilled, (s, { payload }) => { s.budgets = payload || []; })
      .addCase(fetchBudgetsThunk.rejected, (s, { payload }) => { s.budgetsError = payload || 'Failed to load budgets'; })
      .addCase(upsertBudgetThunk.fulfilled, (s, { payload }) => {
        const i = s.budgets.findIndex((b) => b.id === payload.id);
        if (i >= 0) s.budgets[i] = payload;
        else s.budgets.push(payload);
      })
      .addCase(deleteBudgetThunk.fulfilled, (s, { payload: id }) => {
        s.budgets = s.budgets.filter((b) => b.id !== id);
      })

      .addCase(fetchGenerationTraceThunk.pending, (s) => {
        s.generationTraceLoading = true; s.generationTrace = null; s.generationTraceError = null;
      })
      .addCase(fetchGenerationTraceThunk.fulfilled, (s, { payload }) => {
        s.generationTraceLoading = false; s.generationTrace = payload;
      })
      .addCase(fetchGenerationTraceThunk.rejected, (s, { payload }) => {
        s.generationTraceLoading = false; s.generationTraceError = payload || 'Failed to load trace';
      });
  },
});

export const { clearError, setFilters, setFeedbackScope, resetFilters, clearGenerationTrace } = analyticsSlice.actions;
export default analyticsSlice.reducer;

export const selectSummary    = (s) => s.analytics.summary;
export const selectProjectRows = (s) => s.analytics.projectRows;
export const selectGenHistory = (s) => s.analytics.genHistory;
export const selectPromptVersionHistory = (s) => s.analytics.promptVersionHistory;
export const selectDocUploadHistory = (s) => s.analytics.docUploadHistory;
export const selectCddBpHistory = (s) => s.analytics.cddBpHistory;
export const selectHistoryExtrasError = (s) => s.analytics.historyExtrasError;
export const selectFeedbackSummary = (s) => s.analytics.feedbackSummary;
export const selectFeedback   = (s) => s.analytics.feedback;
export const selectFeedbackScope = (s) => s.analytics.feedbackScope;
export const selectReviews    = (s) => s.analytics.reviews;
export const selectLlmCost    = (s) => s.analytics.llmCost;
export const selectPermissionsOverview = (s) => s.analytics.permissionsOverview;
export const selectClearPresets = (s) => s.analytics.clearPresets;
export const selectBudgets = (s) => s.analytics.budgets;
export const selectBudgetsError = (s) => s.analytics.budgetsError;
export const selectGenerationTrace = (s) => s.analytics.generationTrace;
export const selectGenerationTraceLoading = (s) => s.analytics.generationTraceLoading;
export const selectGenerationTraceError = (s) => s.analytics.generationTraceError;
export const selectAnalyticsFilters = (s) => s.analytics.filters;
export const selectAnalyticsLoading = (s) => s.analytics.isLoading;
export const selectAnalyticsError   = (s) => s.analytics.error;
