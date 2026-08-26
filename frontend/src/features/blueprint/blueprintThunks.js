import { createAsyncThunk } from '@reduxjs/toolkit';
import { blueprintService } from './services/blueprintService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { resolveProjectId } from '@utils/workspaceContext';
import { createBlockJobThunks } from '@features/shared/blockJob';
import { createArchiveThunks } from '@features/shared/documentArchive';
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

/**
 * Block-wide (digest-pipeline) Block Blueprint generation — enqueue + poll via
 * the shared factory (same robust polling lifecycle as CDD; see
 * @features/shared/blockJob). Server persists AND pins on completion.
 */
export const {
  generateThunk: generateBlueprintBlockThunk,
  pollThunk: pollBlueprintJobThunk,
  resumeThunk: resumeBlueprintJobThunk,
} =
  createBlockJobThunks({
    prefix: 'blueprint',
    deliverable: 'blueprint',
    enqueue: (payload) => blueprintService.generateBlueprintBlock(payload),
    getJobStatus: (jobId) => blueprintService.getJobStatus(jobId),
    getActiveJob: (courseId) => blueprintService.getActiveBlockJob(courseId),
    getProgress: (jobId) => blueprintService.getBlockJobProgress(jobId),
    // Lets resumeThunk refuse to start a duplicate poll chain for a job it is
    // already polling (one per remount would mean one success toast per remount).
    selectBlockJob: (state) => state.blueprint?.blockJob,
    completedMessage: 'Block Blueprint generated and set as active.',
    failedMessage: 'Blueprint generation failed.',
    onComplete: (dispatch, courseId) => {
      if (courseId) dispatch(fetchBlueprintsThunk(courseId));
    },
  });

/**
 * Import an existing Outline file. Mirrors generateBlueprintThunk's outcome — the
 * server persists AND pins the imported Outline (a new version for that day, or a
 * new Outline), and this returns the full blueprint object so the slice can drop
 * it into the list and set it active, showing it exactly like a generated one.
 */
export const importBlueprintThunk = createAsyncThunk(
  'blueprint/import',
  async (payload, { rejectWithValue }) => {
    try {
      if (!payload?.projectId) {
        return rejectWithValue('Select a project before importing an Outline.');
      }
      const result = await blueprintService.importBlueprint(payload, payload.onProgress);
      toast.success('Outline imported and set as active.');
      // Degraded import (e.g. the file couldn't be structured and came in as one
      // section) — tell the user rather than showing only the success toast.
      if (result?.importWarnings?.length) {
        toast(result.importWarnings[0], { icon: '⚠️' });
      }
      return result;
    } catch (e) {
      // Non-technical fallback per CAS-98 AC #5 — the server's own 400 message
      // (unsupported type, empty file, undetermined day) is surfaced when present.
      return rejectWithValue(
        extractErrorMessage(e)
        || "We couldn't process this file. Please check the file format and try again.",
      );
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
  async ({
    blueprintId, sectionKey, sectionContent, feedback, modelChoice, teacherMode,
  }, { rejectWithValue }) => {
    try {
      const result = await blueprintService.regenerateSection(blueprintId, {
        sectionKey, sectionContent, feedback, modelChoice, teacherMode,
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

/**
 * The archived blueprints for a course, kept apart from the live ones.
 *
 * Deliberately not merged into `blueprints` behind a flag: that array feeds the
 * module picker and the generation flow, and one missed filter there would put
 * a retired document back into a prompt.
 */
export const fetchArchivedBlueprintsThunk = createAsyncThunk(
  'blueprint/fetchArchived',
  async (courseId, { getState, rejectWithValue }) => {
    try {
      const items = await blueprintService.listBlueprints({
        courseId: Number(courseId),
        projectId: resolveProjectId(getState) ?? undefined,
        includeArchived: true,
      });
      return items.filter((b) => b.is_archived);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/**
 * Archive / restore / permanently delete. Same rules and refusal handling as
 * CDDs — see features/shared/documentArchive.js.
 */
const blueprintArchiveThunks = createArchiveThunks({
  name: 'blueprint',
  label: 'Blueprint',
  api: {
    archive: blueprintService.archiveBlueprint,
    restore: blueprintService.restoreBlueprint,
    purge: blueprintService.purgeBlueprint,
    bulkArchive: blueprintService.bulkArchiveBlueprints,
  },
  refetch: (courseId) => async (dispatch) => {
    await Promise.all([
      dispatch(fetchBlueprintsThunk(courseId)),
      dispatch(fetchArchivedBlueprintsThunk(courseId)),
    ]);
  },
});

export const archiveBlueprintThunk = blueprintArchiveThunks.archiveThunk;
export const restoreBlueprintThunk = blueprintArchiveThunks.restoreThunk;
export const purgeBlueprintThunk = blueprintArchiveThunks.purgeThunk;
export const bulkArchiveBlueprintsThunk = blueprintArchiveThunks.bulkArchiveThunk;
export { blueprintArchiveThunks };
