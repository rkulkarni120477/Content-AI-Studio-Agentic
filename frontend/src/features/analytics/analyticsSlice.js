import { createSlice } from '@reduxjs/toolkit';
import {
  fetchSummaryThunk, fetchAuditTrailThunk,
  fetchFeedbackThunk, fetchUsersThunk,
  createUserThunk, toggleUserActiveThunk,
  fetchProjectAnalyticsThunk,
  fetchGenerationHistoryThunk,
  fetchHistoryExtrasThunk, fetchFeedbackSummaryThunk,
  fetchReviewsThunk, fetchLlmCostThunk,
  fetchPermissionsOverviewThunk, fetchClearPresetsThunk,
  fetchAuditTrailFiltersThunk,
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
  auditTrail: { items: [], total: 0, page: 1, page_size: 25, pages: 0 },
  auditTrailError: null,
  auditTrailLoading: false,
  auditFilterOptions: { actors: [], actions: [], entity_types: [], projects: [], page_sizes: [25, 50, 100] },
  auditFilters: {
    actor: '', action: '', entityType: '', projectId: '', dateFrom: '', dateTo: '', pageSize: 25,
  },
  users:      [],
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
  isLoadingUsers: false,
  error:      null,
};

const analyticsSlice = createSlice({
  name: 'analytics',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    setFilters(s, { payload }) { s.filters = { ...s.filters, ...payload }; },
    setFeedbackScope(s, { payload }) { s.feedbackScope = payload; },
    resetFilters(s) { s.filters = initialState.filters; },
    setAuditFilters(s, { payload }) { s.auditFilters = { ...s.auditFilters, ...payload }; },
    clearGenerationTrace(s) { s.generationTrace = null; s.generationTraceError = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchSummaryThunk.pending,   (s) => { s.isLoading = true; })
      .addCase(fetchSummaryThunk.fulfilled,  (s, { payload }) => { s.isLoading = false; s.summary = payload; })
      .addCase(fetchSummaryThunk.rejected,   (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(fetchProjectAnalyticsThunk.fulfilled, (s, { payload }) => { s.projectRows = payload || []; })
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

      .addCase(fetchAuditTrailThunk.pending, (s) => { s.auditTrailLoading = true; s.auditTrailError = null; })
      .addCase(fetchAuditTrailThunk.fulfilled, (s, { payload }) => {
        s.auditTrailLoading = false;
        s.auditTrail = payload ?? { items: [], total: 0 };
      })
      .addCase(fetchAuditTrailThunk.rejected, (s, { payload }) => {
        s.auditTrailLoading = false;
        s.auditTrailError = payload || 'Failed to load audit trail';
      })

      .addCase(fetchAuditTrailFiltersThunk.fulfilled, (s, { payload }) => {
        s.auditFilterOptions = payload || s.auditFilterOptions;
      })

      .addCase(fetchUsersThunk.pending,   (s) => { s.isLoadingUsers = true; })
      .addCase(fetchUsersThunk.fulfilled, (s, { payload }) => { s.isLoadingUsers = false; s.users = payload?.items || payload || []; })
      .addCase(fetchUsersThunk.rejected,  (s) => { s.isLoadingUsers = false; })

      .addCase(fetchPermissionsOverviewThunk.fulfilled, (s, { payload }) => { s.permissionsOverview = payload; })
      .addCase(fetchClearPresetsThunk.fulfilled, (s, { payload }) => { s.clearPresets = payload || []; })

      .addCase(createUserThunk.fulfilled, (s, { payload }) => { s.users.unshift(payload); })
      .addCase(toggleUserActiveThunk.fulfilled, (s, { payload }) => {
        s.users = s.users.map((u) => u.id === payload.id ? payload : u);
      })

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

export const { clearError, setFilters, setFeedbackScope, resetFilters, setAuditFilters, clearGenerationTrace } = analyticsSlice.actions;
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
export const selectAuditTrail = (s) => s.analytics.auditTrail;
export const selectAuditTrailLoading = (s) => s.analytics.auditTrailLoading;
export const selectAuditTrailError = (s) => s.analytics.auditTrailError;
export const selectAuditFilterOptions = (s) => s.analytics.auditFilterOptions;
export const selectAuditFilters = (s) => s.analytics.auditFilters;
export const selectUsers      = (s) => s.analytics.users;
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
