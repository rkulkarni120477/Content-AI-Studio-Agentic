import { createAsyncThunk } from '@reduxjs/toolkit';
import { blueprintService } from './services/blueprintService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage } from '@utils/helpers';
import { resolveProjectId } from '@utils/workspaceContext';
import toast from 'react-hot-toast';

export const fetchBlueprintsThunk = createAsyncThunk(
  'blueprint/fetch',
  async (courseId, { getState, rejectWithValue }) => {
    try {
      const cid = Number(courseId);
      let projectId = resolveProjectId(getState);
      if (!projectId && cid) {
        try {
          const course = await dashboardService.getCourse(cid);
          projectId = course?.project_id ?? null;
        } catch { /* ignore */ }
      }
      const items = await blueprintService.listBlueprints({
        courseId: cid,
        projectId,
      });
      const course = getState()?.dashboard?.selectedCourse;
      let activeBlueprint = null;
      const activeId = course?.active_blueprint_id;
      if (activeId) {
        try {
          activeBlueprint = await blueprintService.getBlueprint(activeId);
        } catch {
          activeBlueprint = items.find((b) => b.id === activeId) || null;
        }
      }
      return { items, activeBlueprint };
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const generateBlueprintThunk = createAsyncThunk(
  'blueprint/generate',
  async (payload, { rejectWithValue }) => {
    try {
      if (!payload?.project_id) {
        return rejectWithValue('Select a project before generating a Blueprint.');
      }
      const result = await blueprintService.generateBlueprint(payload);
      toast.success('Blueprint generated and set as active.');
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const setActiveBlueprintThunk = createAsyncThunk(
  'blueprint/setActive',
  async ({ blueprintId, courseId }, { rejectWithValue }) => {
    try {
      const result = await blueprintService.pinBlueprint(blueprintId, courseId);
      toast.success('Active blueprint updated.');
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const fetchBlueprintVersionsThunk = createAsyncThunk(
  'blueprint/fetchVersions',
  async (blueprintId, { rejectWithValue }) => {
    try {
      return await blueprintService.getVersions(blueprintId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const commitBlueprintVersionThunk = createAsyncThunk(
  'blueprint/commitVersion',
  async ({ blueprintId, data }, { rejectWithValue }) => {
    try {
      const result = await blueprintService.commitVersion(blueprintId, data);
      toast.success('Blueprint version saved.');
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const exportBlueprintThunk = createAsyncThunk(
  'blueprint/export',
  async ({ blueprintId, format }, { rejectWithValue }) => {
    try {
      return await blueprintService.exportBlueprint(blueprintId, format);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const fetchBlueprintComponentsThunk = createAsyncThunk(
  'blueprint/fetchComponents',
  async (blueprintId, { rejectWithValue }) => {
    try {
      return await blueprintService.getComponents(blueprintId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);
