import { createAsyncThunk } from '@reduxjs/toolkit';
import toast from 'react-hot-toast';
import { extractErrorMessage } from '@utils/helpers';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';
import { labelsFromState } from '@config/tenantLabels';

/** Lazy import avoids a circular dep with jobsThunks (which imports JOB_POLL_INTERVAL_MS). */
function trackEnqueuedJob(dispatch, payload) {
  import('@features/jobs/jobsThunks').then(({ trackAndPollJob }) => {
    trackAndPollJob(dispatch, payload);
  });
}

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

/**
 * Per-request timeout for the poll calls ONLY — deliberately not the app-wide
 * `VITE_API_TIMEOUT_MS` (120s), which these used to inherit.
 *
 * A status poll is one indexed DB read. If it has not answered in 15s the answer
 * is not coming, and the useful response is to try again on the next tick rather
 * than block the chain. Inheriting the app default made each stalled poll occupy
 * two full minutes, so the four-error allowance below — meant to ride out blips —
 * could stretch to eight minutes of a frozen UI before it gave up.
 */
export const POLL_REQUEST_TIMEOUT_MS =
  Number(import.meta.env.VITE_JOB_POLL_TIMEOUT_MS) || 15_000;

/** Pass to `api.get` for poll requests. Exported so both features share one value. */
export const POLL_REQUEST_CONFIG = { timeout: POLL_REQUEST_TIMEOUT_MS };

/**
 * Consecutive failed polls tolerated before the UI stops trying.
 *
 * Raised from 4. With the short timeout above this is ~2 minutes of grace at the
 * 2s interval, where 4 was chosen against fast-failing errors and gave up while
 * the server was still working. Giving up no longer claims the job failed — see
 * `lostContact` — so erring long costs nothing but a spinner.
 */
export const MAX_POLL_ERRORS = 20;

/**
 * Build the {generate, poll} thunk pair for one deliverable.
 *
 * @param {object}   cfg
 * @param {string}   cfg.prefix        redux action-type prefix, e.g. 'cdd'
 * @param {string}   cfg.deliverable   'cdd' | 'blueprint' (labels only)
 * @param {function} cfg.enqueue       (payload) => Promise<{job_id, status}>
 * @param {function} cfg.getJobStatus  (jobId) => Promise<jobStatus>
 * @param {function} cfg.onComplete    (dispatch, courseId) => void   // e.g. refetch list
 * @param {string|function} cfg.completedMessage  toast on success; fn receives labels
 * @param {string|function} cfg.failedMessage     toast on failure; fn receives labels
 * @param {string}   [cfg.jobType]     when set, registers with the global JobTracker
 *                                     and suppresses local completion/failure toasts
 */
export function createBlockJobThunks(cfg) {
  const {
    prefix, deliverable, enqueue, getJobStatus, getActiveJob, getProgress,
    selectBlockJob, onComplete,
    completedMessage, failedMessage,
    jobType,
  } = cfg;

  function resolveMsg(msg, getState) {
    if (typeof msg === 'function') return msg(labelsFromState(getState));
    return msg;
  }

  function deliverableLabel(getState) {
    const L = labelsFromState(getState);
    return deliverable === 'blueprint' ? L.blueprint : L.cdd;
  }

  const pollThunk = createAsyncThunk(
    `${prefix}/pollBlockJob`,
    async ({ jobId, courseId, errorCount = 0 }, { dispatch, rejectWithValue, getState }) => {
      try {
        const status = await getJobStatus(jobId);
        // Day counts, when the caller wired them up. Fetched alongside the status
        // rather than folded into it because the status endpoint is used by every job
        // type and must stay one fast DB read. Deliberately awaited AFTER the status
        // and allowed to fail silently: the poll's job is to detect completion, and a
        // progress nicety must never delay or break that.
        if (getProgress && !isTerminalJobStatus(status.status)) {
          try {
            const p = await getProgress(jobId);
            if (p?.progress?.total) status.days = p.progress;
          } catch { /* progress is optional — never fail a poll over it */ }
        }
        if (!isTerminalJobStatus(status.status)) {
          setTimeout(
            () => dispatch(pollThunk({ jobId, courseId, errorCount: 0 })),
            JOB_POLL_INTERVAL_MS,
          );
          return status;
        }
        // When jobType is set the global JobTracker owns completion toasts.
        if (status.status === JOB_STATUSES.COMPLETED) {
          if (!jobType) {
            const done = resolveMsg(completedMessage, getState);
            if (done) toast.success(done);
          }
          if (onComplete) onComplete(dispatch, courseId, getState);
        } else if (status.status === JOB_STATUSES.CANCELLED) {
          if (!jobType) toast(`${deliverableLabel(getState)} generation cancelled.`);
        } else if (!jobType) {
          toast.error(status.error_message || resolveMsg(failedMessage, getState));
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
        // Out of retries — but this says NOTHING about the job, which is still
        // running on the server. Only the browser's view of it has stopped.
        //
        // This distinction is the whole point of the change. Reported as a failure,
        // it produced exactly the wrong conclusion on 2026-08-14: six block-wide
        // builds completed server-side (CDDs 162-167, 113-204s each) while the user
        // was told "generation failed" and retried four times, each retry paying for
        // another full build. `lostContact` lets the UI say what is true — we stopped
        // watching — and point at the reattach that already exists (resumeThunk runs
        // on mount, so a reload picks the build back up).
        return rejectWithValue({
          lostContact: true,
          message: extractErrorMessage(e),
        });
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
        if (jobType) {
          trackEnqueuedJob(dispatch, {
            job_id: res.job_id,
            jobId: res.job_id,
            job_type: jobType,
            jobType,
            course_id: payload.course_id,
            courseId: payload.course_id,
            status: res.status || JOB_STATUSES.QUEUED,
            block: payload.block,
          });
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
    async ({ courseId }, { dispatch, getState }) => {
      if (!getActiveJob || !courseId) return null;
      // Never start a SECOND poll chain for a job already being polled. Chains are
      // setTimeout→dispatch loops on the store and are never cancelled, so they
      // outlive unmount: navigating away from the page and back would otherwise add
      // one poller per visit, and on completion every chain would fire its own
      // success toast and its own list refetch. An explicit selector rather than a
      // search of the state tree, because both features keep a `blockJob` and a
      // search could inspect the other one's.
      if (selectBlockJob && selectBlockJob(getState())?.jobId) return null;
      try {
        const res = await getActiveJob(courseId);
        const job = res?.job ?? null;
        if (!job?.job_id) return null;
        if (jobType) {
          trackEnqueuedJob(dispatch, {
            job_id: job.job_id,
            jobId: job.job_id,
            job_type: jobType,
            jobType,
            course_id: courseId,
            courseId,
            status: job.status || JOB_STATUSES.RUNNING,
            block: job.block,
          });
        }
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
          // Which block this adopted build is for. A course can hold several, and
          // /active matches on course + job_type only, so naming it is what stops a
          // page showing "Block 3" silently reporting a Block 2 completion.
          block: payload.block ?? null,
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
        // Carried forward for the same reason as startedAt: this object is replaced
        // wholesale each poll, and a tick where the progress fetch failed would
        // otherwise blank the day counter mid-build.
        days: payload?.days ?? s.blockJob?.days ?? null,
        block: payload?.block ?? s.blockJob?.block ?? null,
        // Gaps in a SUCCESSFUL result (e.g. days whose extraction failed). Not an
        // error — the deliverable exists and is usable — but "Done" on its own
        // misrepresents it, and the incomplete rows carry defaults rather than
        // blanks, so nothing in the document looks missing.
        warning: payload?.warning ?? null,
      };
      if (isTerminalJobStatus(payload?.status)) s[busyFlag] = false;
    })
    .addCase(pollThunk.rejected, (s, { payload }) => {
      s[busyFlag] = false;
      // Losing contact is not a failed job. Marking the blockJob FAILED here made
      // the panel report a build that was still running — and that finished fine —
      // as an error, which is what drove four needless retries on 2026-08-14.
      // Leave the last known status intact and flag the disconnect separately, so
      // the panel can tell the user the build continues and a reload reattaches.
      if (payload?.lostContact) {
        if (s.blockJob) s.blockJob = { ...s.blockJob, lostContact: true };
        // Not surfaced as a page-level `error` either: that renders the full
        // ErrorState over the list, which reads as "everything broke" when in fact
        // only the poll stopped.
        return;
      }
      s.error = payload;
      if (s.blockJob) s.blockJob = { ...s.blockJob, status: JOB_STATUSES.FAILED };
    });
}
