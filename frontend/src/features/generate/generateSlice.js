import { createSlice } from '@reduxjs/toolkit';
import { launchGenerationThunk, pollJobThunk, cancelJobThunk, invalidatePollSession } from './generateThunks';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';

const initialState = {
  activeJobId:         null,
  jobCourseId:         null,  // course (title) the active job was launched on
  jobStatus:           null,  // pending | running | completed | failed
  jobProgress:         [],    // list of stage messages
  jobProgressPct:      0,
  jobErrorDetail:      null,
  latestBlocks:        [],    // blocks from the last generation
  latestGenerationId:  null,  // generation_id from the last completed job
  isGenerating:        false,
  error:               null,
};

const generateSlice = createSlice({
  name: 'generate',
  initialState,
  reducers: {
    clearJob(s) {
      s.activeJobId  = null;
      s.jobCourseId  = null;
      s.jobStatus    = null;
      s.jobProgress  = [];
      s.jobProgressPct = 0;
      s.jobErrorDetail = null;
    },
    clearError(s)   { s.error = null; },
    addJobStage(s, { payload }) {
      if (payload && !s.jobProgress.includes(payload)) s.jobProgress.push(payload);
    },
  },
  extraReducers: (b) => {
    b
      .addCase(launchGenerationThunk.pending, (s, action) => {
        invalidatePollSession();
        s.isGenerating = true;
        // Tag the job with the title it belongs to, so the banner/results and the
        // background poll only surface on this title — never on another one the
        // user navigates to while it runs.
        s.jobCourseId = action.meta.arg?.course_id ?? null;
        s.error = null;
        s.jobErrorDetail = null;
        s.latestBlocks = [];
        s.jobProgress = [];
        s.jobProgressPct = 0;
      })
      .addCase(launchGenerationThunk.fulfilled, (s, { payload }) => {
        s.activeJobId = payload.job_id;
        s.jobStatus = payload.status || 'queued';
        s.isGenerating = true;
      })
      .addCase(launchGenerationThunk.rejected, (s, { payload }) => {
        s.isGenerating = false;
        s.error = payload;
      })

      .addCase(pollJobThunk.fulfilled, (s, { payload }) => {
        if (
          s.jobStatus === JOB_STATUSES.CANCELLED
          && payload?.status
          && !isTerminalJobStatus(payload.status)
        ) {
          return;
        }
        s.jobStatus = payload.status;
        s.jobProgressPct = payload.progress ?? s.jobProgressPct;
        if (payload.current_step && !s.jobProgress.includes(payload.current_step)) {
          s.jobProgress.push(payload.current_step);
        }
        if (payload.status === JOB_STATUSES.COMPLETED || payload.status === 'completed') {
          s.jobProgressPct = 100;
          s.latestBlocks = payload.blocks || [];
          s.latestGenerationId = payload.generation_id ?? null;
          s.isGenerating = false;
          s.jobErrorDetail = null;
        }
        if (payload.status === JOB_STATUSES.FAILED || payload.status === 'failed') {
          s.error = payload.error_message || payload.error || 'Generation failed.';
          s.jobErrorDetail = payload.error_message || payload.error || null;
          s.isGenerating = false;
        }
        if (payload.status === JOB_STATUSES.CANCELLED || payload.status === 'cancelled') {
          s.isGenerating = false;
        }
      })
      .addCase(pollJobThunk.rejected, (s, { payload }) => {
        s.isGenerating = false;
        s.error = payload;
      })

      .addCase(cancelJobThunk.pending, (s) => {
        invalidatePollSession();
        s.jobStatus = JOB_STATUSES.CANCELLED;
        s.isGenerating = false;
      })
      .addCase(cancelJobThunk.fulfilled, (s) => {
        s.jobStatus = JOB_STATUSES.CANCELLED;
        s.isGenerating = false;
      })
      .addCase(cancelJobThunk.rejected, (s, { payload }) => {
        s.error = payload;
        s.jobStatus = JOB_STATUSES.RUNNING;
        s.isGenerating = true;
      });
  },
});

export const { clearJob, clearError, addJobStage } = generateSlice.actions;
export default generateSlice.reducer;

export const selectActiveJobId        = (s) => s.generate.activeJobId;
export const selectJobCourseId        = (s) => s.generate.jobCourseId;
export const selectJobStatus          = (s) => s.generate.jobStatus;
export const selectJobProgress        = (s) => s.generate.jobProgress;
export const selectJobProgressPct     = (s) => s.generate.jobProgressPct;
export const selectJobErrorDetail     = (s) => s.generate.jobErrorDetail;
export const selectLatestBlocks       = (s) => s.generate.latestBlocks;
export const selectLatestGenerationId = (s) => s.generate.latestGenerationId;
export const selectIsGenerating       = (s) => s.generate.isGenerating;
export const selectGenerateError      = (s) => s.generate.error;
