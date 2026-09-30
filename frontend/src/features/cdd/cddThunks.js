import { createAsyncThunk } from '@reduxjs/toolkit';
import { cddService } from './services/cddService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { resolveProjectId } from '@utils/workspaceContext';
import { queueDeferredToast } from '@utils/deferredToast';
import { createBlockJobThunks } from '@features/shared/blockJob';
import { createArchiveThunks } from '@features/shared/documentArchive';
import { labelsFromState } from '@config/tenantLabels';
import { trackAndPollJob, waitForJobTerminal } from '@features/jobs/jobsThunks';
import { markJobNotified } from '@features/jobs/jobsSlice';
import { JOB_STATUSES } from '@utils/constants';
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
  async (payload, { dispatch, getState, rejectWithValue }) => {
    try {
      const L = labelsFromState(getState);
      if (!payload?.project_id) {
        return rejectWithValue(`Select a project before generating a ${L.cdd}.`);
      }
      const accepted = await cddService.generateCdd(payload);
      // Async (202): register with JobTracker; list refresh + toast happen there.
      if (accepted?.job_id) {
        trackAndPollJob(dispatch, {
          job_id: accepted.job_id,
          jobId: accepted.job_id,
          job_type: 'cdd',
          jobType: 'cdd',
          course_id: payload.course_id,
          courseId: payload.course_id,
          status: accepted.status || 'queued',
        });
        return accepted;
      }
      // Legacy sync response (full CDD object).
      toast.success(`${L.cdd} generated and set as active.`);
      queueDeferredToast(`${L.cdd} created and pinned as active.`);
      return accepted;
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
    jobType: 'cdd_block',
    enqueue: (payload) => cddService.generateCddBlock(payload),
    getJobStatus: (jobId) => cddService.getJobStatus(jobId),
    getActiveJob: (courseId) => cddService.getActiveBlockJob(courseId),
    getProgress: (jobId) => cddService.getBlockJobProgress(jobId),
    // Lets resumeThunk refuse to start a duplicate poll chain for a job it is
    // already polling (one per remount would mean one success toast per remount).
    selectBlockJob: (state) => state.cdd?.blockJob,
    completedMessage: (L) => `${L.cdd} generated and set as active.`,
    failedMessage: (L) => `${L.cdd} generation failed.`,
    onComplete: (dispatch, courseId, getState) => {
      const L = labelsFromState(getState);
      queueDeferredToast(`${L.cdd} created and pinned as active.`);
      if (courseId) dispatch(fetchCddsThunk(courseId));
    },
  });

/**
 * Import an existing Blueprint/CDD file. Mirrors generateCddThunk's outcome — the
 * server persists AND pins the imported CDD, and this returns the full CDD object
 * so the slice can drop it into the list and set it active, showing it in
 * "Your Title Design Documents" exactly like a freshly generated one.
 */
export const importCddThunk = createAsyncThunk(
  'cdd/import',
  async (payload, { getState, rejectWithValue }) => {
    try {
      const L = labelsFromState(getState);
      if (!payload?.projectId) {
        return rejectWithValue(`Select a project before importing a ${L.blueprintLower}.`);
      }
      const result = await cddService.importCdd(payload, payload.onProgress);
      toast.success(`${L.cdd} imported and set as active.`);
      queueDeferredToast(`${L.cdd} imported and pinned as active.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const setActiveCddThunk = createAsyncThunk(
  'cdd/setActive',
  async ({ cddId, courseId }, { getState, rejectWithValue }) => {
    try {
      const result = await cddService.setActiveCdd(cddId, courseId);
      toast.success(`Active ${labelsFromState(getState).cdd} updated.`);
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

/**
 * Report a regeneration's outcome exactly once.
 *
 * The thunk owns the toast, because it is the only layer that sees both the cost
 * and the outcome. Previously it announced "Section regenerated. Used $0.05"
 * whenever a usage summary came back, and the page then separately reported that
 * nothing had changed — so a no-op produced a green success and a red failure
 * side by side, contradicting each other, and the honest half looked like the
 * mistake.
 *
 * A no-op is not a success: nothing was saved and the instruction was not
 * carried out. The cost is still reported, because it was still incurred.
 *
 * Success toasts for async jobs are left to JobTracker; this still handles the
 * no-op (changed === false) case and sync fallbacks.
 */
function notifyRegenOutcome(result, label, { skipSuccessToast = false } = {}) {
  const usageMsg = formatUsageSummaryMessage(result?.usage_summary);
  if (result?.changed === false) {
    const why = result.note
      || `No change — the ${label.toLowerCase()} was returned unchanged. Nothing was saved.`;
    toast.error([why, usageMsg].filter(Boolean).join(' '));
    return;
  }
  if (skipSuccessToast || !usageMsg) return;
  if (hasOverBudget(result.usage_summary)) toast.error(`${label} regenerated. ${usageMsg}`);
  else toast.success(`${label} regenerated. ${usageMsg}`);
}

function courseIdFromState(getState) {
  return getState()?.dashboard?.selectedCourse?.id ?? null;
}

async function pollRegenJob({
  dispatch, getState, accepted, jobType, label, getJobStatus,
}) {
  // Sync fallback (legacy response with updated_content, no job_id).
  if (accepted?.updated_content != null && !accepted?.job_id) {
    notifyRegenOutcome(accepted, label);
    return accepted;
  }
  const jobId = accepted?.job_id;
  if (!jobId) {
    throw new Error('Regeneration did not return a job id.');
  }
  const courseId = courseIdFromState(getState);
  trackAndPollJob(dispatch, {
    job_id: jobId,
    jobId,
    job_type: jobType,
    jobType,
    course_id: courseId,
    courseId,
    status: accepted.status || JOB_STATUSES.QUEUED,
  });

  const status = await waitForJobTerminal(getJobStatus, jobId);
  if (status.status === JOB_STATUSES.FAILED || status.status === JOB_STATUSES.CANCELLED) {
    throw new Error(status.error_message || `${label} regeneration failed.`);
  }
  const result = {
    ...(status.result || {}),
    usage_summary: status.usage_summary ?? status.result?.usage_summary,
  };
  // No-op: suppress the generic JobTracker success toast and report honestly.
  if (result.changed === false) {
    dispatch(markJobNotified(jobId));
    notifyRegenOutcome(result, label);
  }
  // Success: JobTracker toasts (with usage when present).
  return result;
}

export const regenerateCddItemThunk = createAsyncThunk(
  'cdd/regenerateItem',
  async ({ cddId, sectionKey, sectionContent, itemIndex, feedback, useSources, modelChoice },
          { dispatch, getState, rejectWithValue }) => {
    try {
      const accepted = await cddService.regenerateItem(cddId, {
        sectionKey, sectionContent, itemIndex, feedback, useSources, modelChoice,
      });
      return await pollRegenJob({
        dispatch,
        getState,
        accepted,
        jobType: 'cdd_regen_item',
        label: 'Item',
        getJobStatus: (id) => cddService.getJobStatus(id),
      });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const regenerateCddSectionThunk = createAsyncThunk(
  'cdd/regenerateSection',
  async ({ cddId, sectionKey, feedback, modelChoice }, { dispatch, getState, rejectWithValue }) => {
    try {
      const accepted = await cddService.regenerateSection(cddId, { sectionKey, feedback, modelChoice });
      return await pollRegenJob({
        dispatch,
        getState,
        accepted,
        jobType: 'cdd_regen_section',
        label: 'Section',
        getJobStatus: (id) => cddService.getJobStatus(id),
      });
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

/**
 * The archived CDDs for a course, kept in a separate list from the live ones.
 *
 * Deliberately not merged into `cdds` behind a flag: that array feeds the
 * "select a CDD" dropdown and the generation flow, and one missed filter there
 * would put a retired document back into a prompt.
 */
export const fetchArchivedCddsThunk = createAsyncThunk(
  'cdd/fetchArchived',
  async (courseId, { getState, rejectWithValue }) => {
    try {
      const cid = Number(courseId);
      const items = await cddService.listCdds(cid, {
        project_id: resolveProjectId(getState) ?? undefined,
        course_id: cid,
        include_archived: true,
      });
      return items.filter((c) => c.is_archived);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/**
 * Archive / restore / permanently delete. The rules and the refusal handling
 * are shared with Blueprints — see features/shared/documentArchive.js.
 *
 * Each one refetches both lists on success: archiving moves a row from one to
 * the other, so refreshing only the list that was showing would leave the other
 * stale until the next navigation.
 */
const cddArchiveThunks = createArchiveThunks({
  name: 'cdd',
  label: 'CDD',
  labelKey: 'cdd',
  api: {
    archive: cddService.archiveCdd,
    restore: cddService.restoreCdd,
    purge: cddService.purgeCdd,
    bulkArchive: cddService.bulkArchiveCdds,
  },
  refetch: (courseId) => async (dispatch) => {
    await Promise.all([
      dispatch(fetchCddsThunk(courseId)),
      dispatch(fetchArchivedCddsThunk(courseId)),
    ]);
  },
});

export const archiveCddThunk = cddArchiveThunks.archiveThunk;
export const restoreCddThunk = cddArchiveThunks.restoreThunk;
export const purgeCddThunk = cddArchiveThunks.purgeThunk;
export const bulkArchiveCddsThunk = cddArchiveThunks.bulkArchiveThunk;
export { cddArchiveThunks };
