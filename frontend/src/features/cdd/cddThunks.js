import { createAsyncThunk } from '@reduxjs/toolkit';
import { cddService } from './services/cddService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { resolveProjectId } from '@utils/workspaceContext';
import { queueDeferredToast } from '@utils/deferredToast';
import { createBlockJobThunks } from '@features/shared/blockJob';
import toast from 'react-hot-toast';

export const fetchCddsThunk = createAsyncThunk(
  'cdd/fetch',
  async (courseId, { getState, rejectWithValue }) => {
    try {
      const cid = Number(courseId);
      let projectId = resolveProjectId(getState);
      if (!projectId && cid) {
        try {
          const course = await dashboardService.getCourse(cid);
          projectId = course?.project_id ?? null;
        } catch {
          /* course lookup failed — list without project filter */
        }
      }
      const items = await cddService.listCdds(cid, {
        project_id: projectId ?? undefined,
        course_id: cid,
      });
      let activeCdd = null;
      if (cid) {
        try {
          activeCdd = await cddService.getActiveCddForCourse(cid);
        } catch {
          const course = getState()?.dashboard?.selectedCourse;
          const activeId = course?.id === cid ? course.active_cdd_id : null;
          if (activeId) {
            try {
              activeCdd = await cddService.getCdd(activeId);
            } catch {
              activeCdd = items.find((c) => c.id === activeId) || null;
            }
          }
        }
      }
      return { items, activeCdd };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const generateCddThunk = createAsyncThunk(
  'cdd/generate',
  async (payload, { rejectWithValue }) => {
    try {
      if (!payload?.project_id) {
        return rejectWithValue('Select a project before generating a CDD.');
      }
      const result = await cddService.generateCdd(payload);
      toast.success('CDD generated and set as active.');
      queueDeferredToast('CDD created and pinned as active.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/**
 * Block-wide (digest-pipeline) CDD generation — long-running, so it enqueues a
 * job and polls. The server persists AND pins the CDD on completion, so the
 * poller only refreshes the list. All polling-lifecycle robustness (terminal/
 * cancelled handling, transient-error tolerance, missing-job_id guard) lives in
 * the shared factory so CDD and Blueprint can't drift apart.
 */
export const {
  generateThunk: generateCddBlockThunk,
  pollThunk: pollCddJobThunk,
  resumeThunk: resumeCddJobThunk,
} =
  createBlockJobThunks({
    prefix: 'cdd',
    deliverable: 'cdd',
    enqueue: (payload) => cddService.generateCddBlock(payload),
    getJobStatus: (jobId) => cddService.getJobStatus(jobId),
    getActiveJob: (courseId) => cddService.getActiveBlockJob(courseId),
    completedMessage: 'CDD generated and set as active.',
    failedMessage: 'CDD generation failed.',
    onComplete: (dispatch, courseId) => {
      queueDeferredToast('CDD created and pinned as active.');
      if (courseId) dispatch(fetchCddsThunk(courseId));
    },
  });

export const setActiveCddThunk = createAsyncThunk(
  'cdd/setActive',
  async ({ cddId, courseId }, { rejectWithValue }) => {
    try {
      const result = await cddService.setActiveCdd(cddId, courseId);
      toast.success('Active CDD updated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchCddVersionsThunk = createAsyncThunk(
  'cdd/fetchVersions',
  async (cddId, { rejectWithValue }) => {
    try { return await cddService.getVersions(cddId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const activateCddVersionThunk = createAsyncThunk(
  'cdd/activateVersion',
  async ({ cddId, version, courseId }, { rejectWithValue, dispatch }) => {
    try {
      await cddService.activateVersion(cddId, version);
      toast.success(`Version ${version} is now active.`);
      const detail = await cddService.getCdd(cddId);
      dispatch(fetchCddVersionsThunk(cddId));
      return { detail, courseId };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const commitCddVersionThunk = createAsyncThunk(
  'cdd/commitVersion',
  async ({ cddId, data }, { rejectWithValue }) => {
    try {
      const result = await cddService.commitVersion(cddId, data);
      toast.success('New version saved.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const regenerateCddItemThunk = createAsyncThunk(
  'cdd/regenerateItem',
  async ({ cddId, sectionKey, sectionContent, itemIndex, feedback, modelChoice }, { rejectWithValue }) => {
    try {
      const result = await cddService.regenerateItem(cddId, {
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

export const regenerateCddSectionThunk = createAsyncThunk(
  'cdd/regenerateSection',
  async ({ cddId, sectionKey, feedback, modelChoice }, { rejectWithValue }) => {
    try {
      const result = await cddService.regenerateSection(cddId, { sectionKey, feedback, modelChoice });
      const usageMsg = formatUsageSummaryMessage(result.usage_summary);
      if (usageMsg) {
        if (hasOverBudget(result.usage_summary)) toast.error(`Section regenerated. ${usageMsg}`);
        else toast.success(`Section regenerated. ${usageMsg}`);
      }
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const exportCddThunk = createAsyncThunk(
  'cdd/export',
  async ({ cddId, format }, { rejectWithValue }) => {
    try { return await cddService.exportCdd(cddId, format); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
