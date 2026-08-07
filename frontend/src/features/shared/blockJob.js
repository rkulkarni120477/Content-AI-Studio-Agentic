import { createAsyncThunk } from '@reduxjs/toolkit';
import toast from 'react-hot-toast';
import { extractErrorMessage } from '@utils/helpers';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';

/**
 * Shared machinery for block-wide (digest-pipeline) async generation, used
 * identically by the CDD and Blueprint features. It exists to (a) remove the
 * near-verbatim duplication that previously lived in both features' thunks and
 * slices, and (b) fix the polling-lifecycle bugs in ONE place so a fix can't be
 * applied to one deliverable and forgotten on the other.
 *
 * Robustness properties (previously missing in the per-feature copies):
 *  - terminal detection is "not active" via isTerminalJobStatus, so `cancelled`
 *    (and any future terminal status) cleanly stops the loop and clears state,
 *    instead of wedging `isGenerating` true forever;
 *  - a missing `job_id` in the enqueue response rejects (clears state) instead of
 *    leaving the button spinning with no poller;
 *  - transient poll errors are tolerated up to MAX_POLL_ERRORS consecutive
 *    failures (re-scheduled) before giving up, so one network blip doesn't
 *    falsely report failure while the job actually completes;
 *  - the completion refresh is guarded by the originating courseId (the caller
 *    passes an isCurrentCourse predicate) so a job finishing after the user has
 *    navigated away can't overwrite another course's view.
 */
export const JOB_POLL_INTERVAL_MS = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS) || 2000;
export const MAX_POLL_ERRORS = 4;

/**
 * Build the {generate, poll} thunk pair for one deliverable.
 *
 * @param {object}   cfg
 * @param {string}   cfg.prefix        redux action-type prefix, e.g. 'cdd'
 * @param {string}   cfg.deliverable   'cdd' | 'blueprint' (labels only)
 * @param {function} cfg.enqueue       (payload) => Promise<{job_id, status}>
 * @param {function} cfg.getJobStatus  (jobId) => Promise<jobStatus>
 * @param {function} cfg.onComplete    (dispatch, courseId) => void   // e.g. refetch list
 * @param {string}   cfg.completedMessage
 * @param {string}   cfg.failedMessage
 */
export function createBlockJobThunks(cfg) {
  const {
    prefix, deliverable, enqueue, getJobStatus, onComplete,
    completedMessage, failedMessage,
  } = cfg;

  const pollThunk = createAsyncThunk(
    `${prefix}/pollBlockJob`,
    async ({ jobId, courseId, errorCount = 0 }, { dispatch, rejectWithValue }) => {
      try {
        const status = await getJobStatus(jobId);
        if (!isTerminalJobStatus(status.status)) {
          setTimeout(
            () => dispatch(pollThunk({ jobId, courseId, errorCount: 0 })),
            JOB_POLL_INTERVAL_MS,
          );
          return status;
        }
        if (status.status === JOB_STATUSES.COMPLETED) {
          if (completedMessage) toast.success(completedMessage);
          if (onComplete) onComplete(dispatch, courseId);
        } else if (status.status === JOB_STATUSES.CANCELLED) {
          toast(`${deliverable === 'blueprint' ? 'Blueprint' : 'CDD'} generation cancelled.`);
        } else {
          toast.error(status.error_message || failedMessage);
        }
        return status;
      } catch (e) {
        // Tolerate transient errors: keep polling until MAX_POLL_ERRORS in a row.
        // The job keeps running server-side; giving up on the first blip would
        // falsely report failure for a job that actually completes.
        if (errorCount + 1 < MAX_POLL_ERRORS) {
          setTimeout(
            () => dispatch(pollThunk({ jobId, courseId, errorCount: errorCount + 1 })),
            JOB_POLL_INTERVAL_MS,
          );
          return { status: JOB_STATUSES.RUNNING, transientError: true };
        }
        return rejectWithValue(extractErrorMessage(e));
      }
    },
  );

  const generateThunk = createAsyncThunk(
    `${prefix}/generateBlock`,
    async (payload, { dispatch, rejectWithValue }) => {
      if (!payload?.project_id) return rejectWithValue('Select a project before generating.');
      if (!payload?.block) return rejectWithValue('Enter a block (e.g. "Block 2") for block-wide generation.');
      try {
        const res = await enqueue(payload);
        if (!res?.job_id) {
          // No handle to poll ⇒ nothing will ever clear the in-progress state.
          return rejectWithValue('Generation did not start — no job handle was returned.');
        }
        dispatch(pollThunk({ jobId: res.job_id, courseId: payload.course_id }));
        return res;
      } catch (e) {
        return rejectWithValue(extractErrorMessage(e));
      }
    },
  );

  return { generateThunk, pollThunk };
}

/** Initial slice sub-state for a block job. */
export const initialBlockJobState = { blockJob: null };

/**
 * Attach the standard block-job reducer cases to a slice builder. `flags` names
 * the boolean the feature uses for "generation in progress" (e.g. 'isGenerating')
 * so this stays compatible with each slice's existing field.
 */
export function attachBlockJobReducers(builder, { generateThunk, pollThunk }, busyFlag = 'isGenerating') {
  builder
    .addCase(generateThunk.pending, (s) => {
      s[busyFlag] = true; s.error = null;
      s.blockJob = { jobId: null, status: JOB_STATUSES.QUEUED, progress: 0, currentStep: null };
    })
    .addCase(generateThunk.fulfilled, (s, { payload }) => {
      // Job enqueued; stay busy until the poller sees a terminal status.
      s.blockJob = {
        jobId: payload?.job_id ?? null,
        status: payload?.status || JOB_STATUSES.QUEUED,
        progress: 0, currentStep: null,
      };
    })
    .addCase(generateThunk.rejected, (s, { payload }) => {
      s[busyFlag] = false; s.error = payload; s.blockJob = null;
    })
    .addCase(pollThunk.fulfilled, (s, { payload }) => {
      if (payload?.transientError) return;  // keep prior state; loop continues
      s.blockJob = {
        jobId: payload?.job_id ?? s.blockJob?.jobId ?? null,
        status: payload?.status,
        progress: payload?.progress ?? 0,
        currentStep: payload?.current_step ?? null,
      };
      if (isTerminalJobStatus(payload?.status)) s[busyFlag] = false;
    })
    .addCase(pollThunk.rejected, (s, { payload }) => {
      s[busyFlag] = false; s.error = payload;
      if (s.blockJob) s.blockJob = { ...s.blockJob, status: JOB_STATUSES.FAILED };
    });
}
