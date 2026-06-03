import { createAsyncThunk } from '@reduxjs/toolkit';
import { analyticsService } from './services/analyticsService';
import { adminService } from './services/adminService';
import { buildAnalyticsParams } from './utils/analyticsParams';
import { buildAuditTrailParams } from './utils/auditTrailParams';
import { extractErrorMessage } from '@utils/helpers';
import { downloadBlob } from '@utils/helpers';
import toast from 'react-hot-toast';

function paramsFrom(filters, { getState }) {
  return buildAnalyticsParams(filters, getState);
}

export const fetchSummaryThunk = createAsyncThunk(
  'analytics/fetchSummary',
  async (filters, { getState, rejectWithValue }) => {
    try {
      return await analyticsService.getSummary(paramsFrom(filters, { getState }));
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchProjectAnalyticsThunk = createAsyncThunk(
  'analytics/fetchProjects',
  async (_, { rejectWithValue }) => {
    try { return await analyticsService.getProjectAnalytics(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchPromptPerfThunk = createAsyncThunk(
  'analytics/fetchPromptPerf',
  async (filters, { getState, rejectWithValue }) => {
    try {
      return await analyticsService.getPromptPerf(paramsFrom(filters, { getState }));
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchQualityTrendsThunk = createAsyncThunk(
  'analytics/fetchQualityTrends',
  async (filters, { getState, rejectWithValue }) => {
    try {
      return await analyticsService.getQualityTrends(paramsFrom(filters, { getState }));
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchGenerationHistoryThunk = createAsyncThunk(
  'analytics/fetchGenHistory',
  async (filters, { getState, rejectWithValue }) => {
    try {
      return await analyticsService.getGenerationHistory({
        ...paramsFrom(filters, { getState }),
        limit: 20,
      });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchHistoryExtrasThunk = createAsyncThunk(
  'analytics/fetchHistoryExtras',
  async (_, { rejectWithValue }) => {
    const results = await Promise.allSettled([
      analyticsService.getPromptVersionHistory({ limit: 20 }),
      analyticsService.getDocumentUploadHistory({ limit: 20 }),
      analyticsService.getCddBlueprintHistory({ limit: 40 }),
    ]);

    const pick = (r) => (r.status === 'fulfilled' ? r.value : []);
    const errors = results
      .filter((r) => r.status === 'rejected')
      .map((r) => extractErrorMessage(r.reason));

    const payload = {
      promptVersions: pick(results[0]),
      docUploads: pick(results[1]),
      cddBpEvents: pick(results[2]),
      errors,
    };

    if (errors.length === results.length) {
      return rejectWithValue(errors.join('; '));
    }
    return payload;
  },
);

export const fetchFeedbackSummaryThunk = createAsyncThunk(
  'analytics/fetchFeedbackSummary',
  async (_, { rejectWithValue }) => {
    try { return await analyticsService.getFeedbackSummary(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchFeedbackThunk = createAsyncThunk(
  'analytics/fetchFeedback',
  async ({ scope, page = 1 }, { rejectWithValue }) => {
    try {
      const params = { page, page_size: 25 };
      if (scope === 'learning') params.scope = 'learning';
      if (scope === 'one_time') params.scope = 'one_time';
      return await analyticsService.getFeedback(params);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchReviewsThunk = createAsyncThunk(
  'analytics/fetchReviews',
  async (_, { rejectWithValue }) => {
    try { return await analyticsService.getReviews({ page: 1, page_size: 20 }); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchSystemLogsThunk = createAsyncThunk(
  'analytics/fetchSystemLogs',
  async ({ page = 1 } = {}, { rejectWithValue }) => {
    try {
      return await analyticsService.getSystemLogs({ page, page_size: 50 });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchLlmCostThunk = createAsyncThunk(
  'analytics/fetchLlmCost',
  async (filters, { getState, rejectWithValue }) => {
    try {
      return await analyticsService.getLlmCost(paramsFrom(filters, { getState }));
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchAuditTrailFiltersThunk = createAsyncThunk(
  'analytics/fetchAuditFilters',
  async (_, { rejectWithValue }) => {
    try { return await analyticsService.getAuditTrailFilters(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchAuditTrailThunk = createAsyncThunk(
  'analytics/fetchAudit',
  async ({ page = 1, pageSize = 25, filters = {} } = {}, { rejectWithValue }) => {
    try {
      return await analyticsService.getAuditTrail(
        buildAuditTrailParams({ page, pageSize, filters }),
      );
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const exportAuditThunk = createAsyncThunk(
  'analytics/exportAudit',
  async ({ filters = {} } = {}, { rejectWithValue }) => {
    try {
      const response = await analyticsService.exportAudit(
        buildAuditTrailParams({ page: 1, pageSize: 5000, filters }),
      );
      downloadBlob(response.data, 'audit-trail.csv');
      toast.success('Audit trail exported.');
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchUsersThunk = createAsyncThunk(
  'analytics/fetchUsers',
  async (_, { rejectWithValue }) => {
    try { return await analyticsService.listUsers(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const createUserThunk = createAsyncThunk(
  'analytics/createUser',
  async (data, { rejectWithValue }) => {
    try {
      const result = await analyticsService.createUser(data);
      toast.success('User created.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const toggleUserActiveThunk = createAsyncThunk(
  'analytics/toggleUser',
  async (userId, { rejectWithValue }) => {
    try {
      const result = await analyticsService.toggleUserActive(userId);
      toast.success('User status updated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchPermissionsOverviewThunk = createAsyncThunk(
  'analytics/fetchPermissionsOverview',
  async (_, { rejectWithValue }) => {
    try { return await adminService.getPermissionsOverview(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchClearPresetsThunk = createAsyncThunk(
  'analytics/fetchClearPresets',
  async (_, { rejectWithValue }) => {
    try { return await adminService.listClearPresets(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const clearDatabaseThunk = createAsyncThunk(
  'analytics/clearDatabase',
  async (tag, { rejectWithValue }) => {
    try {
      const result = await adminService.clearPreset(tag);
      toast.success(result?.message || 'Data cleared.');
      return tag;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
