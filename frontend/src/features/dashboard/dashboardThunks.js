import { createAsyncThunk } from '@reduxjs/toolkit';
import { dashboardService } from './services/dashboardService';
import { extractErrorMessage } from '@utils/helpers';
import { applyWorkspaceConfig } from './dashboardSlice';

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
    try { return await dashboardService.listCourses(clusterId); }
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
  async (courseId, { dispatch, rejectWithValue }) => {
    try {
      const config = await dashboardService.getCourseConfig(courseId);
      dispatch(applyWorkspaceConfig(config));
      return config;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const saveWorkspaceConfigThunk = createAsyncThunk(
  'dashboard/saveWorkspaceConfig',
  async ({ courseId, config }, { rejectWithValue }) => {
    try { return await dashboardService.saveCourseConfig(courseId, config); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
