import { createSlice } from '@reduxjs/toolkit';
import {
  fetchSummaryThunk, fetchUsageThunk, fetchAuditTrailThunk,
  fetchCostThunk, fetchFeedbackThunk, fetchUsersThunk,
  createUserThunk, toggleUserActiveThunk,
  fetchProjectAnalyticsThunk, fetchPromptPerfThunk,
  fetchQualityTrendsThunk, fetchGenerationHistoryThunk,
} from './analyticsThunks';

const initialState = {
  summary:    null,
  projectRows: [],
  promptPerf: [],
  qualityRatings: [],
  genHistory: [],
  usage:      null,
  cost:       null,
  feedback:   { items: [], total: 0 },
  auditTrail: { items: [], total: 0 },
  users:      [],
  filters: {
    dateRange:  'last_30_days',
    model:      '',
    userId:     null,
    projectId:  null,
    entityType: '',
    status:     '',
    page:       1,
    pageSize:   20,
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
    resetFilters(s) { s.filters = initialState.filters; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchSummaryThunk.pending,   (s) => { s.isLoading = true; })
      .addCase(fetchSummaryThunk.fulfilled, (s, { payload }) => { s.isLoading = false; s.summary = payload; })
      .addCase(fetchSummaryThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(fetchProjectAnalyticsThunk.fulfilled, (s, { payload }) => { s.projectRows = payload || []; })
      .addCase(fetchPromptPerfThunk.fulfilled, (s, { payload }) => { s.promptPerf = payload || []; })
      .addCase(fetchQualityTrendsThunk.fulfilled, (s, { payload }) => {
        s.qualityRatings = payload?.ratings || payload || [];
      })
      .addCase(fetchGenerationHistoryThunk.fulfilled, (s, { payload }) => { s.genHistory = payload || []; })

      .addCase(fetchUsageThunk.fulfilled,   (s, { payload }) => { s.usage = payload; })
      .addCase(fetchCostThunk.fulfilled,    (s, { payload }) => { s.cost = payload; })
      .addCase(fetchFeedbackThunk.fulfilled,(s, { payload }) => { s.feedback = payload; })
      .addCase(fetchAuditTrailThunk.fulfilled, (s, { payload }) => { s.auditTrail = payload; })

      .addCase(fetchUsersThunk.pending,   (s) => { s.isLoadingUsers = true; })
      .addCase(fetchUsersThunk.fulfilled, (s, { payload }) => { s.isLoadingUsers = false; s.users = payload; })
      .addCase(fetchUsersThunk.rejected,  (s) => { s.isLoadingUsers = false; })

      .addCase(createUserThunk.fulfilled, (s, { payload }) => { s.users.unshift(payload); })
      .addCase(toggleUserActiveThunk.fulfilled, (s, { payload }) => {
        s.users = s.users.map((u) => u.id === payload.id ? payload : u);
      });
  },
});

export const { clearError, setFilters, resetFilters } = analyticsSlice.actions;
export default analyticsSlice.reducer;

export const selectSummary    = (s) => s.analytics.summary;
export const selectProjectRows = (s) => s.analytics.projectRows;
export const selectPromptPerf = (s) => s.analytics.promptPerf;
export const selectQualityRatings = (s) => s.analytics.qualityRatings;
export const selectGenHistory = (s) => s.analytics.genHistory;
export const selectUsage      = (s) => s.analytics.usage;
export const selectCost       = (s) => s.analytics.cost;
export const selectFeedback   = (s) => s.analytics.feedback;
export const selectAuditTrail = (s) => s.analytics.auditTrail;
export const selectUsers      = (s) => s.analytics.users;
export const selectAnalyticsFilters = (s) => s.analytics.filters;
export const selectAnalyticsLoading = (s) => s.analytics.isLoading;
export const selectAnalyticsError   = (s) => s.analytics.error;
