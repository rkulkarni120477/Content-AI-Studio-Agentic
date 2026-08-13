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
    prefix, deliverable, enqueue, getJobStatus, getActiveJob, onComplete,
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

  /**
   * Reattach to a build already running server-side. Dispatched on mount.
   *
   * The poll chain above lives only in browser memory, so a refresh, a closed
   * laptop, or a transient network error orphaned the UI while the job kept running
   * — the user then either watched a dead spinner or re-submitted and paid for a
   * second concurrent build (observed 2026-08-13 on a 22-minute Block 2 build).
   *
   * Resolves to null when nothing is in flight, and swallows its own errors: this is
   * a background convenience on every page load, so a failed lookup must leave the
   * page exactly as it would have been rather than surfacing an error the user did
   * not ask for. `getActiveJob` is optional so a caller that has not wired it up
   * keeps working unchanged.
   */
  const resumeThunk = createAsyncThunk(
    `${prefix}/resumeBlockJob`,
    async ({ courseId }, { dispatch }) => {
      if (!getActiveJob || !courseId) return null;
      try {
        const res = await getActiveJob(courseId);
        const job = res?.job ?? null;
        if (!job?.job_id) return null;
        dispatch(pollThunk({ jobId: job.job_id, courseId }));
        return job;
      } catch {
        return null;   // never let a resume attempt break a page load
      }
    },
  );

  return { generateThunk, pollThunk, resumeThunk };
}

/** Initial slice sub-state for a block job. */
export const initialBlockJobState = { blockJob: null };

/**
 * Attach the standard block-job reducer cases to a slice builder. `flags` names
 * the boolean the feature uses for "generation in progress" (e.g. 'isGenerating')
 * so this stays compatible with each slice's existing field.
 */
export function attachBlockJobReducers(builder, { generateThunk, pollThunk, resumeThunk },
                                       busyFlag = 'isGenerating') {
  if (resumeThunk) {
    builder
      // A resume that FINDS a job must put the slice into exactly the state a fresh
      // enqueue would, so the existing progress UI lights up with no extra branches.
      // A resume that finds nothing must change nothing at all — mount-time lookups
      // run on every page load, including the overwhelming majority where no build is
      // running, and must never clear a state the user is looking at.
      .addCase(resumeThunk.fulfilled, (s, { payload }) => {
        if (!payload?.job_id) return;
        s[busyFlag] = true;
        s.error = null;
        s.blockJob = {
          jobId: payload.job_id,
          status: payload.status,
          progress: payload.progress ?? 0,
          currentStep: payload.current_step ?? null,
          // Carried so the UI can show how long the build has been going. A cold
          // block build shows one step for minutes, so elapsed time is the only
          // signal that distinguishes "working" from "wedged".
          startedAt: payload.created_at ?? null,
          resumed: true,
        };
      });
    // Deliberately no `rejected` case: the thunk already swallows its errors and
    // resolves null. A failed background lookup is not the user's problem.
  }
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
        // Preserved across polls, not rebuilt from each response. This object is
        // replaced wholesale every ~2s, so anything not carried forward is lost —
        // which silently dropped the resumed job's start time on its first poll. The
        // status endpoint returns created_at too, so a freshly enqueued job gets an
        // elapsed clock as well, not just a resumed one.
        startedAt: payload?.created_at ?? s.blockJob?.startedAt ?? null,
        resumed: s.blockJob?.resumed ?? false,
        // Gaps in a SUCCESSFUL result (e.g. days whose extraction failed). Not an
        // error — the deliverable exists and is usable — but "Done" on its own
        // misrepresents it, and the incomplete rows carry defaults rather than
        // blanks, so nothing in the document looks missing.
        warning: payload?.warning ?? null,
      };
      if (isTerminalJobStatus(payload?.status)) s[busyFlag] = false;
    })
    .addCase(pollThunk.rejected, (s, { payload }) => {
      s[busyFlag] = false; s.error = payload;
      if (s.blockJob) s.blockJob = { ...s.blockJob, status: JOB_STATUSES.FAILED };
    });
}
