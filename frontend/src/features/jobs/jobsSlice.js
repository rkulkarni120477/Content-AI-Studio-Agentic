import { createSlice } from '@reduxjs/toolkit';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';

const DISMISSED_KEY = 'cas_dismissed_jobs';

function loadDismissed() {
  try {
    const raw = sessionStorage.getItem(DISMISSED_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveDismissed(ids) {
  try {
    sessionStorage.setItem(DISMISSED_KEY, JSON.stringify(ids.slice(-80)));
  } catch {
    /* ignore quota */
  }
}

function normalizeJob(raw) {
  if (!raw) return null;
  const jobId = raw.job_id || raw.jobId || raw.id;
  if (!jobId) return null;
  return {
    jobId: String(jobId),
    status: raw.status || JOB_STATUSES.QUEUED,
    progress: raw.progress ?? 0,
    currentStep: raw.current_step ?? raw.currentStep ?? null,
    jobType: raw.job_type ?? raw.jobType ?? null,
    courseId: raw.course_id ?? raw.courseId ?? null,
    generationId: raw.generation_id ?? raw.generationId ?? null,
    errorMessage: raw.error_message ?? raw.errorMessage ?? null,
    warning: raw.warning ?? null,
    block: raw.block ?? null,
    createdAt: raw.created_at ?? raw.createdAt ?? null,
    updatedAt: raw.updated_at ?? raw.updatedAt ?? null,
    usageSummary: raw.usage_summary ?? raw.usageSummary ?? null,
    result: raw.result ?? null,
    // Local-only: toast already shown for this terminal transition
    notified: Boolean(raw.notified),
  };
}

const initialState = {
  jobsById: {},
  dismissedIds: loadDismissed(),
  pollCourseId: null,
  isResuming: false,
  lastError: null,
};

const jobsSlice = createSlice({
  name: 'jobs',
  initialState,
  reducers: {
    trackJob(state, action) {
      const job = normalizeJob(action.payload);
      if (!job) return;
      const prev = state.jobsById[job.jobId];
      state.jobsById[job.jobId] = {
        ...prev,
        ...job,
        notified: prev?.notified ?? false,
      };
      // Re-show if user re-ran the same type after dismissing an old id
      state.dismissedIds = state.dismissedIds.filter((id) => id !== job.jobId);
      saveDismissed(state.dismissedIds);
    },
    upsertJobs(state, action) {
      const list = Array.isArray(action.payload) ? action.payload : [];
      for (const raw of list) {
        const job = normalizeJob(raw);
        if (!job) continue;
        const prev = state.jobsById[job.jobId];
        state.jobsById[job.jobId] = {
          ...prev,
          ...job,
          // Preserve local notified flag across server refreshes
          notified: prev?.notified ?? false,
          // Prefer richer local result if server omitted it this poll
          result: job.result ?? prev?.result ?? null,
        };
      }
    },
    markJobNotified(state, action) {
      const id = String(action.payload);
      if (state.jobsById[id]) state.jobsById[id].notified = true;
    },
    dismissJob(state, action) {
      const id = String(action.payload);
      if (!state.dismissedIds.includes(id)) {
        state.dismissedIds.push(id);
        saveDismissed(state.dismissedIds);
      }
    },
    dismissAllTerminal(state) {
      const ids = Object.values(state.jobsById)
        .filter((j) => isTerminalJobStatus(j.status))
        .map((j) => j.jobId);
      const set = new Set([...state.dismissedIds, ...ids]);
      state.dismissedIds = [...set];
      saveDismissed(state.dismissedIds);
    },
    setPollCourseId(state, action) {
      state.pollCourseId = action.payload ?? null;
    },
    setJobsResuming(state, action) {
      state.isResuming = Boolean(action.payload);
    },
    setJobsError(state, action) {
      state.lastError = action.payload || null;
    },
    clearJob(state, action) {
      const id = String(action.payload);
      delete state.jobsById[id];
    },
  },
});

export const {
  trackJob,
  upsertJobs,
  markJobNotified,
  dismissJob,
  dismissAllTerminal,
  setPollCourseId,
  setJobsResuming,
  setJobsError,
  clearJob,
} = jobsSlice.actions;

export const selectJobsById = (state) => state.jobs?.jobsById || {};
export const selectDismissedJobIds = (state) => state.jobs?.dismissedIds || [];
export const selectPollCourseId = (state) => state.jobs?.pollCourseId;

export const selectVisibleJobs = (state) => {
  const byId = selectJobsById(state);
  const dismissed = new Set(selectDismissedJobIds(state));
  return Object.values(byId)
    .filter((j) => !dismissed.has(j.jobId))
    .sort((a, b) => String(b.updatedAt || b.createdAt || '').localeCompare(
      String(a.updatedAt || a.createdAt || ''),
    ));
};

export const selectActiveJobs = (state) =>
  selectVisibleJobs(state).filter((j) => !isTerminalJobStatus(j.status));

export const selectJobBellCount = (state) => selectVisibleJobs(state).length;

export const selectJobById = (jobId) => (state) =>
  selectJobsById(state)[String(jobId)] || null;

export const selectJobsOfType = (jobType, courseId) => (state) => {
  const jobs = Object.values(selectJobsById(state)).filter((j) => j.jobType === jobType);
  if (courseId != null) {
    return jobs.filter((j) => Number(j.courseId) === Number(courseId));
  }
  return jobs;
};

export const selectActiveJobOfType = (jobType, courseId) => (state) => {
  const jobs = selectJobsOfType(jobType, courseId)(state)
    .filter((j) => !isTerminalJobStatus(j.status));
  return jobs[0] || null;
};

export default jobsSlice.reducer;
