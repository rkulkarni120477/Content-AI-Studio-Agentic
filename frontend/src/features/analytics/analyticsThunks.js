import { createAsyncThunk } from '@reduxjs/toolkit';
import { analyticsService } from './services/analyticsService';
import { extractErrorMessage } from '@utils/helpers';
import { downloadBlob } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchSummaryThunk = createAsyncThunk(
  'analytics/fetchSummary',
  async (params, { getState, rejectWithValue }) => {
    try {
      const projectId = params?.project_id ?? getState()?.dashboard?.selectedProject?.id;
      return await analyticsService.getSummary({ project_id: projectId });
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
  async (params, { getState, rejectWithValue }) => {
    try {
      const projectId = params?.project_id ?? getState()?.dashboard?.selectedProject?.id;
      return await analyticsService.getPromptPerf({ project_id: projectId });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchQualityTrendsThunk = createAsyncThunk(
  'analytics/fetchQualityTrends',
  async (params, { getState, rejectWithValue }) => {
    try {
      const projectId = params?.project_id ?? getState()?.dashboard?.selectedProject?.id;
      return await analyticsService.getQualityTrends({ project_id: projectId });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchGenerationHistoryThunk = createAsyncThunk(
  'analytics/fetchGenHistory',
  async (params, { getState, rejectWithValue }) => {
    try {
      const projectId = params?.project_id ?? getState()?.dashboard?.selectedProject?.id;
      return await analyticsService.getGenerationHistory({ project_id: projectId, limit: 20 });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchUsageThunk = createAsyncThunk(
  'analytics/fetchUsage',
  async (params, { rejectWithValue }) => {
    try { return await analyticsService.getUsage(params); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchCostThunk = createAsyncThunk(
  'analytics/fetchCost',
  async (params, { rejectWithValue }) => {
    try { return await analyticsService.getCost(params); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchFeedbackThunk = createAsyncThunk(
  'analytics/fetchFeedback',
  async (params, { rejectWithValue }) => {
    try { return await analyticsService.getFeedback(params); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchAuditTrailThunk = createAsyncThunk(
  'analytics/fetchAudit',
  async (params, { rejectWithValue }) => {
    try { return await analyticsService.getAuditTrail(params); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const exportAuditThunk = createAsyncThunk(
  'analytics/exportAudit',
  async (params, { rejectWithValue }) => {
    try {
      const response = await analyticsService.exportAudit(params);
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
