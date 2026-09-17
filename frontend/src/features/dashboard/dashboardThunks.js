import { createAsyncThunk } from '@reduxjs/toolkit';
import { dashboardService } from './services/dashboardService';
import { extractErrorMessage } from '@utils/helpers';
import { applyWorkspaceConfig } from './dashboardSlice';
import { setToken } from '@features/auth/authSlice';

export const fetchProjectsThunk = createAsyncThunk(
  'dashboard/fetchProjects',
  async (_, { rejectWithValue }) => {
    try { return await dashboardService.listProjects(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchClustersThunk = createAsyncThunk(
  'dashboard/fetchClusters',
  async (projectId, { rejectWithValue }) => {
    try { return await dashboardService.listClusters(projectId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchCoursesThunk = createAsyncThunk(
  'dashboard/fetchCourses',
  async (clusterId, { rejectWithValue }) => {
    try { return await dashboardService.listCourses(clusterId, { includeArchived: true }); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchModelsThunk = createAsyncThunk(
  'dashboard/fetchModels',
  async (_, { rejectWithValue }) => {
    try { return await dashboardService.listModels(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchWorkspaceConfigThunk = createAsyncThunk(
  'dashboard/fetchWorkspaceConfig',
  async (_, { dispatch, rejectWithValue }) => {
    try {
      const ws = await dashboardService.getWorkspace();
      const cfg = ws?.config || {};
      // applyWorkspaceConfig itself refuses to overwrite a model the user already
      // picked this session (stale in-flight JWT hydrate after Apply / select).
      dispatch(applyWorkspaceConfig(cfg));
      return cfg;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const saveWorkspaceConfigThunk = createAsyncThunk(
  'dashboard/saveWorkspaceConfig',
  async ({ config }, { dispatch, rejectWithValue }) => {
    try {
      const res = await dashboardService.updateWorkspaceConfig(config);
      if (res?.access_token) dispatch(setToken(res.access_token));
      return res;
    }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
