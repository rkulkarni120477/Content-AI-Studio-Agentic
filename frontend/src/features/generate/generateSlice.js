import { createSlice } from '@reduxjs/toolkit';
import { runGenerationThunk, queueGenerationThunk, pollJobThunk } from './generateThunks';
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
      .addCase(runGenerationThunk.pending,   (s) => { s.isGenerating = true; s.error = null; s.latestBlocks = []; })
      .addCase(runGenerationThunk.fulfilled, (s, { payload }) => {
        s.isGenerating  = false;
        s.latestBlocks  = payload.blocks || [];
      })
      .addCase(runGenerationThunk.rejected,  (s, { payload }) => { s.isGenerating = false; s.error = payload; })

      .addCase(queueGenerationThunk.fulfilled, (s, { payload }) => {
        s.activeJobId = payload.job_id;
        s.jobStatus   = JOB_STATUSES.PENDING;
      })

      .addCase(pollJobThunk.fulfilled, (s, { payload }) => {
        s.jobStatus = payload.status;
        if (payload.status === JOB_STATUSES.COMPLETED) {
          s.latestBlocks = payload.blocks || [];
          s.isGenerating = false;
        }
        if (payload.status === JOB_STATUSES.FAILED) {
          s.error        = payload.error || 'Generation failed.';
          s.isGenerating = false;
        }
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
