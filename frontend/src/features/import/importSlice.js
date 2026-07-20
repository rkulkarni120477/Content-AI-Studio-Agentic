import { createSlice } from '@reduxjs/toolkit';
import {
  validatePackageThunk,
  startImportThunk,
  pollImportJobThunk,
  fetchImportRecordThunk,
} from './importThunks';

/**
 * Import wizard state (reverse pipeline). Mirrors the generate slice shape:
 * a thin local state + createAsyncThunk lifecycle handling. See reverse_cas.md.
 *
 * step: 'upload' → 'progress' → 'done' | 'error'
 */
const initialState = {
  step: 'upload',
  validating: false,
  validation: null,        // { package_name, course_title, structure_counts, warnings }
  validateError: null,
  starting: false,
  courseId: null,
  importId: null,
  jobId: null,
  job: null,               // { status, progress, current_step, error_message }
  record: null,            // import record fetched on completion (warnings surfacing)
  error: null,
};

const importSlice = createSlice({
  name: 'import',
  initialState,
  reducers: {
    resetImport: () => ({ ...initialState }),
    clearValidation: (s) => {
      s.validation = null;
      s.validateError = null;
    },
  },
  extraReducers: (builder) => {
    builder
      // ── Validate ──────────────────────────────────────────────────
      .addCase(validatePackageThunk.pending, (s) => {
        s.validating = true;
        s.validateError = null;
        s.validation = null;
      })
      .addCase(validatePackageThunk.fulfilled, (s, { payload }) => {
        s.validating = false;
        s.validation = payload;
      })
      .addCase(validatePackageThunk.rejected, (s, { payload }) => {
        s.validating = false;
        s.validateError = payload || 'Could not read that package.';
      })

      // ── Start import ──────────────────────────────────────────────
      .addCase(startImportThunk.pending, (s) => {
        s.starting = true;
        s.error = null;
        s.step = 'progress';
      })
      .addCase(startImportThunk.fulfilled, (s, { payload }) => {
        s.starting = false;
        s.courseId = payload?.course_id ?? null;
        s.importId = payload?.import_id ?? null;
        s.jobId = payload?.job_id ?? null;
        s.job = { status: 'queued', progress: 0, current_step: 'Queued' };
      })
      .addCase(startImportThunk.rejected, (s, { payload }) => {
        s.starting = false;
        s.error = payload || 'Could not start the import.';
        s.step = 'error';
      })

      // ── Poll job ──────────────────────────────────────────────────
      .addCase(pollImportJobThunk.fulfilled, (s, { payload }) => {
        s.job = payload;
        if (payload?.status === 'completed') {
          s.step = 'done';
        } else if (payload?.status === 'failed') {
          s.step = 'error';
          s.error = payload?.error_message || 'Import failed.';
        }
      })
      .addCase(pollImportJobThunk.rejected, (s, { payload }) => {
        s.step = 'error';
        s.error = payload || 'Lost contact with the import job.';
      })

      // ── Import record (warnings surfacing) ────────────────────────
      .addCase(fetchImportRecordThunk.fulfilled, (s, { payload }) => {
        s.record = payload;
      });
  },
});

export const { resetImport, clearValidation } = importSlice.actions;

export const selectImport = (state) => state.import;

export default importSlice.reducer;
