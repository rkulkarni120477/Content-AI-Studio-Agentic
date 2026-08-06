import { createAsyncThunk } from '@reduxjs/toolkit';
import { blueprintService } from './services/blueprintService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { resolveProjectId } from '@utils/workspaceContext';
import toast from 'react-hot-toast';

export const fetchBlueprintsThunk = createAsyncThunk(
  'blueprint/fetch',
  async (courseId, { getState, rejectWithValue }) => {
    try {
      const cid = Number(courseId);
      let projectId = resolveProjectId(getState);
      let freshActiveId = null;
      if (cid) {
        try {
          const course = await dashboardService.getCourse(cid);
          if (!projectId) projectId = course?.project_id ?? null;
          freshActiveId = course?.active_blueprint_id ?? null;
        } catch { /* ignore */ }
      }
      const items = await blueprintService.listBlueprints({
        courseId: cid,
        projectId,
      });
      let activeBlueprint = null;
      const activeId = freshActiveId ?? getState()?.dashboard?.selectedCourse?.active_blueprint_id;
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

export const activateBlueprintVersionThunk = createAsyncThunk(
  'blueprint/activateVersion',
  async ({ blueprintId, version }, { rejectWithValue, dispatch }) => {
    try {
      await blueprintService.activateVersion(blueprintId, version);
      toast.success(`Version ${version} is now active.`);
      const bp = await blueprintService.getBlueprint(blueprintId);
      dispatch(fetchBlueprintVersionsThunk(blueprintId));
      return bp;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
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

export const regenerateBlueprintItemThunk = createAsyncThunk(
  'blueprint/regenerateItem',
  async ({ blueprintId, sectionKey, sectionContent, itemIndex, feedback, modelChoice }, { rejectWithValue }) => {
    try {
      const result = await blueprintService.regenerateItem(blueprintId, {
        sectionKey, sectionContent, itemIndex, feedback, modelChoice,
      });
      const usageMsg = formatUsageSummaryMessage(result.usage_summary);
      if (usageMsg) {
        if (hasOverBudget(result.usage_summary)) toast.error(`Item regenerated. ${usageMsg}`);
        else toast.success(`Item regenerated. ${usageMsg}`);
      }
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const regenerateBlueprintSectionThunk = createAsyncThunk(
  'blueprint/regenerateSection',
  async ({ blueprintId, sectionKey, feedback, modelChoice, teacherMode }, { rejectWithValue }) => {
    try {
      const result = await blueprintService.regenerateSection(blueprintId, {
        sectionKey, feedback, modelChoice, teacherMode,
      });
      const usageMsg = formatUsageSummaryMessage(result.usage_summary);
      if (usageMsg) {
        if (hasOverBudget(result.usage_summary)) toast.error(`Section regenerated. ${usageMsg}`);
        else toast.success(`Section regenerated. ${usageMsg}`);
      }
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
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
