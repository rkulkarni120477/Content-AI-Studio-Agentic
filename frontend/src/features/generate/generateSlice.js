import { createSlice } from '@reduxjs/toolkit';
import { launchGenerationThunk, pollJobThunk } from './generateThunks';
import { JOB_STATUSES } from '@utils/constants';

const initialState = {
  activeJobId:    null,
  jobStatus:      null,  // pending | running | completed | failed
  jobProgress:    [],    // list of stage messages
  latestBlocks:   [],    // blocks from the last generation
  isGenerating:   false,
  error:          null,
};

const generateSlice = createSlice({
  name: 'generate',
  initialState,
  reducers: {
    clearJob(s) {
      s.activeJobId  = null;
      s.jobStatus    = null;
      s.jobProgress  = [];
    },
    clearError(s)   { s.error = null; },
    addJobStage(s, { payload }) { s.jobProgress.push(payload); },
  },
  extraReducers: (b) => {
    b
      .addCase(launchGenerationThunk.pending, (s) => {
        s.isGenerating = true;
        s.error = null;
        s.latestBlocks = [];
        s.jobProgress = [];
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
        s.jobStatus = payload.status;
        if (payload.current_step && !s.jobProgress.includes(payload.current_step)) {
          s.jobProgress.push(payload.current_step);
        }
        if (payload.status === JOB_STATUSES.COMPLETED || payload.status === 'completed') {
          s.latestBlocks = payload.blocks || [];
          s.isGenerating = false;
        }
        if (payload.status === JOB_STATUSES.FAILED || payload.status === 'failed') {
          s.error = payload.error_message || payload.error || 'Generation failed.';
          s.isGenerating = false;
        }
      })
      .addCase(pollJobThunk.rejected, (s, { payload }) => {
        s.isGenerating = false;
        s.error = payload;
      });
  },
});

export const { clearJob, clearError, addJobStage } = generateSlice.actions;
export default generateSlice.reducer;

export const selectActiveJobId   = (s) => s.generate.activeJobId;
export const selectJobStatus     = (s) => s.generate.jobStatus;
export const selectJobProgress   = (s) => s.generate.jobProgress;
export const selectLatestBlocks  = (s) => s.generate.latestBlocks;
export const selectIsGenerating  = (s) => s.generate.isGenerating;
export const selectGenerateError = (s) => s.generate.error;
