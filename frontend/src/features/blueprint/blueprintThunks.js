import { createAsyncThunk } from '@reduxjs/toolkit';
import { blueprintService } from './services/blueprintService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { resolveProjectId } from '@utils/workspaceContext';
import { createBlockJobThunks } from '@features/shared/blockJob';
import { createArchiveThunks } from '@features/shared/documentArchive';
import { labelsFromState } from '@config/tenantLabels';
import { trackAndPollJob, waitForJobTerminal } from '@features/jobs/jobsThunks';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';
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
  async (payload, { dispatch, getState, rejectWithValue }) => {
    try {
      const L = labelsFromState(getState);
      if (!payload?.project_id) {
        return rejectWithValue(`Select a project before generating a ${L.blueprint}.`);
      }
      const accepted = await blueprintService.generateBlueprint(payload);
      // Async (202): JobTracker owns toast + list refresh.
      if (accepted?.job_id) {
        trackAndPollJob(dispatch, {
          job_id: accepted.job_id,
          jobId: accepted.job_id,
          job_type: 'blueprint',
          jobType: 'blueprint',
          course_id: payload.course_id,
          courseId: payload.course_id,
          status: accepted.status || 'queued',
        });
        return accepted;
      }
      toast.success(`${L.blueprint} generated and set as active.`);
      if (accepted?.source_context_unavailable) {
        // Generation degrades rather than failing when the Source Library is
        // unreachable, so the document does exist and is active — the user just
        // has to be told it was written without its sources. Without this the
        // two outcomes are indistinguishable: same success toast, same-looking
        // document, and the only trace is a server log line. toast.error is
        // this codebase's idiom for a non-blocking caution — the same call the
        // Blueprint page already makes for "Link a CDD for best results".
        toast.error(
          `Generated without Source Library grounding: the library could not be `
          + `reached, so this ${L.blueprint} used only the ${L.cdd} and the active `
          + `${L.styleLower}. Regenerate once it is available if you need source-grounded `
          + `content.`,
          { duration: 9000 },
        );
      }
      return accepted;
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
    jobType: 'blueprint_block',
    enqueue: (payload) => blueprintService.generateBlueprintBlock(payload),
    getJobStatus: (jobId) => blueprintService.getJobStatus(jobId),
    getActiveJob: (courseId) => blueprintService.getActiveBlockJob(courseId),
    getProgress: (jobId) => blueprintService.getBlockJobProgress(jobId),
    // Lets resumeThunk refuse to start a duplicate poll chain for a job it is
    // already polling (one per remount would mean one success toast per remount).
    selectBlockJob: (state) => state.blueprint?.blockJob,
    completedMessage: (L) => `Block ${L.blueprint} generated and set as active.`,
    failedMessage: (L) => `${L.blueprint} generation failed.`,
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

// ── Async Outline import (timeout-proof) ─────────────────────────────────────
// The upload POST returns a job handle immediately (no long request → no proxy
// 504); the slow extract + LLM restructure runs in a background worker and we
// poll for the result. This is the path the UI uses.
const IMPORT_POLL_INTERVAL_MS = 2000;
const IMPORT_MAX_POLL_ERRORS = 20;

/** Poll one import job to completion, re-scheduling itself until terminal. */
export const pollOutlineImportJobThunk = createAsyncThunk(
  'blueprint/pollImport',
  async ({ jobId, courseId, errorCount = 0 }, { dispatch, getState, rejectWithValue }) => {
    try {
      const status = await blueprintService.getJobStatus(jobId);
      if (!isTerminalJobStatus(status.status)) {
        setTimeout(
          () => dispatch(pollOutlineImportJobThunk({ jobId, courseId, errorCount: 0 })),
          IMPORT_POLL_INTERVAL_MS,
        );
        return status;
      }
      // JobTracker owns success/failure toasts when the job was registered there.
      const tracked = Boolean(getState()?.jobs?.jobsById?.[String(jobId)]);
      if (status.status === JOB_STATUSES.COMPLETED || status.status === 'completed') {
        if (!tracked) toast.success('Outline imported and set as active.');
        // Degraded (single-section) import — the worker records it as the job warning.
        if (status.warning) toast(status.warning, { icon: '⚠️' });
        if (courseId) dispatch(fetchBlueprintsThunk(courseId));
        if (status.generation_id) {
          try {
            const blueprint = await blueprintService.getBlueprint(status.generation_id);
            return { ...status, blueprint };
          } catch { /* the list refetch above still surfaces the new Outline */ }
        }
      } else if (status.status === JOB_STATUSES.FAILED || status.status === 'failed') {
        // AC #5 non-technical messages — the worker's own message (unsupported
        // type, undetermined day, timeout fallback) is surfaced when present.
        if (!tracked) {
          toast.error(
            status.error_message
            || 'Unable to process the file. The file could not be processed at this time. Please try again.',
          );
        }
      }
      return status;
    } catch (e) {
      // Transient poll error — retry a bounded number of times. The job keeps
      // running server-side; we only lost contact with the status endpoint.
      if (errorCount + 1 < IMPORT_MAX_POLL_ERRORS) {
        setTimeout(
          () => dispatch(pollOutlineImportJobThunk({ jobId, courseId, errorCount: errorCount + 1 })),
          IMPORT_POLL_INTERVAL_MS,
        );
        return { status: 'running', transientError: true };
      }
      return rejectWithValue({ lostContact: true, message: extractErrorMessage(e) });
    }
  },
);

/** Enqueue an async import, then start polling its job. */
export const importBlueprintAsyncThunk = createAsyncThunk(
  'blueprint/importAsync',
  async (payload, { dispatch, rejectWithValue }) => {
    try {
      if (!payload?.projectId) {
        return rejectWithValue('Select a project before importing an Outline.');
      }
      const res = await blueprintService.importBlueprintAsync(payload, payload.onProgress);
      if (!res?.job_id) {
        return rejectWithValue('Import did not start. Please try again.');
      }
      const courseId = Number(payload.courseId);
      trackAndPollJob(dispatch, {
        job_id: res.job_id,
        jobId: res.job_id,
        job_type: 'outline_import',
        jobType: 'outline_import',
        course_id: courseId,
        courseId,
        status: res.status || 'queued',
      });
      dispatch(pollOutlineImportJobThunk({ jobId: res.job_id, courseId }));
      return res;   // { job_id, status, poll_url }
    } catch (e) {
      return rejectWithValue(
        extractErrorMessage(e)
        || "We couldn't process this file. Please check the file format and try again.",
      );
    }
  },
);

/** On mount, reattach to an import already running for this course (survives a refresh). */
export const resumeOutlineImportJobThunk = createAsyncThunk(
  'blueprint/resumeImport',
  async ({ courseId }, { getState, dispatch }) => {
    try {
      if (getState().blueprint?.importJob?.jobId) return null;   // already watching
      const res = await blueprintService.getActiveOutlineImportJob(Number(courseId));
      const job = res?.job ?? null;
      if (job?.job_id) {
        const cid = Number(courseId);
        trackAndPollJob(dispatch, {
          job_id: job.job_id,
          jobId: job.job_id,
          job_type: 'outline_import',
          jobType: 'outline_import',
          course_id: cid,
          courseId: cid,
          status: job.status || 'running',
        });
        dispatch(pollOutlineImportJobThunk({ jobId: job.job_id, courseId: cid }));
        return job;
      }
      return null;
    } catch {
      return null;
    }
  },
);

export const setActiveBlueprintThunk = createAsyncThunk(
  'blueprint/setActive',
  async ({ blueprintId, courseId }, { getState, rejectWithValue }) => {
    try {
      const result = await blueprintService.pinBlueprint(blueprintId, courseId);
      toast.success(`Active ${labelsFromState(getState).blueprint} updated.`);
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
  async ({ blueprintId, data }, { getState, rejectWithValue }) => {
    try {
      const result = await blueprintService.commitVersion(blueprintId, data);
      toast.success(`${labelsFromState(getState).blueprint} version saved.`);
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

async function pollBlueprintRegenJob({
  dispatch, getState, accepted, jobType, label, getJobStatus,
}) {
  if (accepted?.updated_content != null && !accepted?.job_id) {
    const usageMsg = formatUsageSummaryMessage(accepted.usage_summary);
    if (usageMsg) {
      if (hasOverBudget(accepted.usage_summary)) toast.error(`${label} regenerated. ${usageMsg}`);
      else toast.success(`${label} regenerated. ${usageMsg}`);
    }
    return accepted;
  }
  const jobId = accepted?.job_id;
  if (!jobId) throw new Error('Regeneration did not return a job id.');
  const courseId = getState()?.dashboard?.selectedCourse?.id ?? null;
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
  return {
    ...(status.result || {}),
    usage_summary: status.usage_summary ?? status.result?.usage_summary,
  };
}

export const regenerateBlueprintItemThunk = createAsyncThunk(
  'blueprint/regenerateItem',
  async (
    { blueprintId, sectionKey, sectionContent, itemIndex, feedback, modelChoice },
    { dispatch, getState, rejectWithValue },
  ) => {
    try {
      const accepted = await blueprintService.regenerateItem(blueprintId, {
        sectionKey, sectionContent, itemIndex, feedback, modelChoice,
      });
      return await pollBlueprintRegenJob({
        dispatch,
        getState,
        accepted,
        jobType: 'blueprint_regen_item',
        label: 'Item',
        getJobStatus: (id) => blueprintService.getJobStatus(id),
      });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const regenerateBlueprintSectionThunk = createAsyncThunk(
  'blueprint/regenerateSection',
  async ({
    blueprintId, sectionKey, sectionContent, feedback, modelChoice, teacherMode,
  }, { dispatch, getState, rejectWithValue }) => {
    try {
      const accepted = await blueprintService.regenerateSection(blueprintId, {
        sectionKey, sectionContent, feedback, modelChoice, teacherMode,
      });
      return await pollBlueprintRegenJob({
        dispatch,
        getState,
        accepted,
        jobType: 'blueprint_regen_section',
        label: 'Section',
        getJobStatus: (id) => blueprintService.getJobStatus(id),
      });
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
  labelKey: 'blueprint',
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
