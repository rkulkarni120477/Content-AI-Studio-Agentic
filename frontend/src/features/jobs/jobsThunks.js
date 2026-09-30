import { createAsyncThunk } from '@reduxjs/toolkit';
import toast from 'react-hot-toast';
import { jobsService } from './services/jobsService';
import {
  upsertJobs,
  markJobNotified,
  setJobsResuming,
  setJobsError,
  setPollCourseId,
  selectJobsById,
  trackJob,
} from './jobsSlice';
import { jobTypeMeta } from './jobLabels';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';
import { labelsFromState, applyTerminology } from '@config/tenantLabels';
import { JOB_POLL_INTERVAL_MS } from '@features/shared/blockJob';

/** Bumped when course changes or poller stops so stale setTimeouts die. */
let pollGeneration = 0;

export function invalidateJobsPoll() {
  pollGeneration += 1;
  return pollGeneration;
}

function currentPollGeneration() {
  return pollGeneration;
}

/**
 * Register a newly enqueued job with the global tracker and ensure polling runs.
 */
export function trackAndPollJob(dispatch, jobPayload) {
  dispatch(trackJob(jobPayload));
  const courseId = jobPayload.courseId ?? jobPayload.course_id;
  if (courseId != null) {
    dispatch(setPollCourseId(Number(courseId)));
    dispatch(pollActiveJobsThunk({ courseId: Number(courseId) }));
  }
}

/**
 * Block until a job reaches a terminal status. Used by thunks that must apply
 * ``status.result`` (e.g. CDD/Blueprint section regen) while the global tracker
 * still owns the toast / bell UX.
 */
export async function waitForJobTerminal(getJobStatus, jobId, {
  intervalMs = JOB_POLL_INTERVAL_MS,
  maxPolls = 150,
} = {}) {
  for (let i = 0; i < maxPolls; i += 1) {
    const status = await getJobStatus(jobId);
    if (isTerminalJobStatus(status?.status)) return status;
    await new Promise((resolve) => { setTimeout(resolve, intervalMs); });
  }
  const err = new Error('Job timed out. Please try again.');
  err.timedOut = true;
  throw err;
}

function notifyIfNeeded(dispatch, getState, job) {
  if (!job || job.notified || !isTerminalJobStatus(job.status)) return;
  if (job.status === JOB_STATUSES.CANCELLED) {
    dispatch(markJobNotified(job.jobId));
    return;
  }

  const L = labelsFromState(getState);
  const meta = jobTypeMeta(job.jobType);
  let message = job.status === JOB_STATUSES.COMPLETED ? meta.complete : meta.failed;
  if (job.block && job.status === JOB_STATUSES.COMPLETED) {
    message = `${meta.complete} (${job.block})`;
  }
  if (job.status === JOB_STATUSES.FAILED && job.errorMessage) {
    message = `${meta.failed}: ${job.errorMessage}`;
  }
  if (job.status === JOB_STATUSES.COMPLETED && job.usageSummary) {
    const usageMsg = formatUsageSummaryMessage(job.usageSummary);
    if (usageMsg) message = `${message}. ${usageMsg}`;
  }

  message = applyTerminology(message, L);

  if (job.status === JOB_STATUSES.FAILED || hasOverBudget(job.usageSummary)) {
    toast.error(message);
  } else {
    toast.success(message);
  }
  dispatch(markJobNotified(job.jobId));

  // Soft refresh of the related feature lists — best-effort, fire-and-forget.
  try {
    refreshRelated(dispatch, job);
  } catch {
    /* ignore */
  }
}

function refreshRelated(dispatch, job) {
  const courseId = job.courseId;
  if (!courseId) return;
  // Dynamic imports keep the jobs module free of circular deps with feature thunks.
  const type = job.jobType;
  if (type === 'cdd' || type === 'cdd_block' || type === 'cdd_regen_item' || type === 'cdd_regen_section') {
    import('@features/cdd/cddThunks').then(({ fetchCddsThunk }) => {
      dispatch(fetchCddsThunk(courseId));
    });
  }
  if (
    type === 'blueprint'
    || type === 'blueprint_block'
    || type === 'outline_import'
    || type === 'blueprint_regen_item'
    || type === 'blueprint_regen_section'
  ) {
    import('@features/blueprint/blueprintThunks').then(({ fetchBlueprintsThunk }) => {
      if (fetchBlueprintsThunk) dispatch(fetchBlueprintsThunk(courseId));
    });
  }
  if (type === 'style_understand') {
    import('@features/style/styleThunks').then(({ fetchStylesThunk }) => {
      dispatch(fetchStylesThunk());
    });
  }
  if (type === 'import' || type === 'import_reverse') {
    import('@features/cdd/cddThunks').then(({ fetchCddsThunk }) => {
      dispatch(fetchCddsThunk(courseId));
    });
    import('@features/blueprint/blueprintThunks').then(({ fetchBlueprintsThunk }) => {
      if (fetchBlueprintsThunk) dispatch(fetchBlueprintsThunk(courseId));
    });
    import('@features/style/styleThunks').then(({ fetchStylesThunk }) => {
      dispatch(fetchStylesThunk({ courseId }));
    });
  }
  if (type === 'generation') {
    import('@features/generate/generateThunks').then((mod) => {
      if (mod.fetchGenerationsThunk) dispatch(mod.fetchGenerationsThunk(courseId));
    }).catch(() => {});
  }
}

export const resumeJobsThunk = createAsyncThunk(
  'jobs/resume',
  async ({ courseId }, { dispatch, rejectWithValue }) => {
    if (!courseId) return [];
    dispatch(setJobsResuming(true));
    dispatch(setPollCourseId(Number(courseId)));
    try {
      const res = await jobsService.listJobs(courseId, {
        sinceMinutes: 45,
        statuses: 'queued,running,completed,failed,cancelled',
      });
      const list = res?.jobs || res?.items || (Array.isArray(res) ? res : []);
      dispatch(upsertJobs(list));
      dispatch(pollActiveJobsThunk({ courseId: Number(courseId) }));
      return list;
    } catch (e) {
      const msg = extractErrorMessage(e);
      dispatch(setJobsError(msg));
      return rejectWithValue(msg);
    } finally {
      dispatch(setJobsResuming(false));
    }
  },
);

/**
 * Poll every tracked non-terminal job for the course; reschedule while any remain active.
 * Also re-lists occasionally so jobs started in another tab appear.
 */
export const pollActiveJobsThunk = createAsyncThunk(
  'jobs/pollActive',
  async ({ courseId, generation }, { dispatch, getState }) => {
    const gen = generation ?? currentPollGeneration();
    if (gen !== currentPollGeneration()) return null;
    const cid = Number(courseId);
    if (!cid) return null;

    const byId = selectJobsById(getState());
    const active = Object.values(byId).filter(
      (j) => Number(j.courseId) === cid && !isTerminalJobStatus(j.status),
    );

    // Refresh list when we have no local active jobs (reattach / other tab)
    // or periodically alongside status polls.
    try {
      if (active.length === 0) {
        const res = await jobsService.listJobs(cid, {
          sinceMinutes: 45,
          statuses: 'queued,running,completed,failed',
        });
        const list = res?.jobs || [];
        dispatch(upsertJobs(list));
      }
    } catch {
      /* list failure is non-fatal; keep polling known ids */
    }

    const refreshed = selectJobsById(getState());
    const toPoll = Object.values(refreshed).filter(
      (j) => Number(j.courseId) === cid && !isTerminalJobStatus(j.status),
    );

    for (const job of toPoll) {
      try {
        const status = await jobsService.getJobStatus(job.jobId);
        dispatch(upsertJobs([status]));
        const updated = {
          ...normalizeFromStatus(status),
          notified: selectJobsById(getState())[job.jobId]?.notified,
        };
        notifyIfNeeded(dispatch, getState, {
          ...selectJobsById(getState())[job.jobId],
          ...updated,
        });
      } catch (e) {
        dispatch(setJobsError(extractErrorMessage(e)));
      }
    }

    // Notify any jobs that became terminal via list upsert without a per-id poll
    for (const job of Object.values(selectJobsById(getState()))) {
      if (Number(job.courseId) === cid) {
        notifyIfNeeded(dispatch, getState, job);
      }
    }

    const stillActive = Object.values(selectJobsById(getState())).some(
      (j) => Number(j.courseId) === cid && !isTerminalJobStatus(j.status),
    );

    if (stillActive && gen === currentPollGeneration()) {
      setTimeout(() => {
        if (gen === currentPollGeneration()) {
          dispatch(pollActiveJobsThunk({ courseId: cid, generation: gen }));
        }
      }, JOB_POLL_INTERVAL_MS);
    } else if (!stillActive && gen === currentPollGeneration()) {
      // Slow heartbeat so a job started elsewhere still surfaces in the bell
      setTimeout(() => {
        if (gen === currentPollGeneration()) {
          dispatch(pollActiveJobsThunk({ courseId: cid, generation: gen }));
        }
      }, Math.max(JOB_POLL_INTERVAL_MS * 5, 10_000));
    }

    return { active: stillActive };
  },
);

function normalizeFromStatus(status) {
  return {
    jobId: String(status.job_id || status.jobId),
    status: status.status,
    progress: status.progress ?? 0,
    currentStep: status.current_step ?? status.currentStep,
    jobType: status.job_type ?? status.jobType,
    courseId: status.course_id ?? status.courseId,
    generationId: status.generation_id ?? status.generationId,
    errorMessage: status.error_message ?? status.errorMessage,
    warning: status.warning,
    block: status.block,
    result: status.result,
    usageSummary: status.usage_summary ?? status.usageSummary,
    createdAt: status.created_at ?? status.createdAt,
    updatedAt: status.updated_at ?? status.updatedAt,
  };
}

export const cancelTrackedJobThunk = createAsyncThunk(
  'jobs/cancel',
  async (jobId, { dispatch, rejectWithValue }) => {
    try {
      const result = await jobsService.cancelJob(jobId);
      dispatch(upsertJobs([result]));
      toast.success('Job cancelled.');
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);
