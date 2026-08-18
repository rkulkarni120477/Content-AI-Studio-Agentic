import { createSlice } from '@reduxjs/toolkit';
import { attachBlockJobReducers } from '@features/shared/blockJob';
import { attachArchiveReducers } from '@features/shared/documentArchive';
import {
  fetchCddsThunk, generateCddThunk, setActiveCddThunk,
  fetchCddVersionsThunk, commitCddVersionThunk, activateCddVersionThunk,
  generateCddBlockThunk, pollCddJobThunk, resumeCddJobThunk,
  fetchArchivedCddsThunk, cddArchiveThunks,
} from './cddThunks';

const initialState = {
  cdds:       [],
  activeCdd:  null,  // full CDD object with active version
  versions:   [],
  isLoading:  false,
  isGenerating: false,
  error:      null,
  // Block-wide async job tracking (digest pipeline).
  blockJob:   null,  // { jobId, status, progress, currentStep }
  // Archive. Held apart from `cdds` because that array feeds the generation
  // dropdown — a retired document must not be one missed filter away from a prompt.
  archivedCdds: [],
  isArchiving:  false,
  // A refused archive/purge, with the server's reasons. Separate from `error`
  // so the list stays on screen while the user acts on the refusal.
  archiveRefusal: null,
};

const cddSlice = createSlice({
  name: 'cdd',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    clearArchiveRefusal(s) { s.archiveRefusal = null; },
    clearGenerating(s) { s.isGenerating = false; },
    setActiveCddLocal(s, { payload }) { s.activeCdd = payload; },
    // Clear stale block-job status so a completed/failed banner from one course
    // doesn't leak into another course's view on navigation.
    resetBlockJob(s) { s.blockJob = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchCddsThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchCddsThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.cdds = payload?.items ?? payload ?? [];
        s.activeCdd = payload?.activeCdd ?? null;
      })
      .addCase(fetchCddsThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(generateCddThunk.pending,   (s) => { s.isGenerating = true; s.error = null; })
      .addCase(generateCddThunk.fulfilled, (s, { payload }) => {
        s.isGenerating = false;
        s.cdds.unshift(payload);
        s.activeCdd = payload;
      })
      .addCase(generateCddThunk.rejected,  (s, { payload }) => { s.isGenerating = false; s.error = payload; })

      .addCase(setActiveCddThunk.fulfilled, (s, { payload }) => { s.activeCdd = payload; })

      .addCase(fetchCddVersionsThunk.fulfilled, (s, { payload }) => { s.versions = payload; })

      .addCase(commitCddVersionThunk.fulfilled, (s, { payload }) => {
        s.versions.unshift(payload);
        if (s.activeCdd) s.activeCdd = { ...s.activeCdd, active_version: payload };
      })

      .addCase(activateCddVersionThunk.fulfilled, (s, { payload }) => {
        if (payload?.detail) {
          s.activeCdd = payload.detail;
          const idx = s.cdds.findIndex((c) => c.id === payload.detail.id);
          if (idx >= 0) s.cdds[idx] = { ...s.cdds[idx], active_version: payload.detail.active_version };
        }
      });

    b.addCase(fetchArchivedCddsThunk.fulfilled, (s, { payload }) => {
      s.archivedCdds = payload ?? [];
    });
    // A failed archived-list load leaves the previous contents rather than
    // emptying them — "no archived CDDs" and "we could not check" must not
    // look the same right before someone decides to regenerate.

    // Shared block-wide async-job cases (pending/fulfilled/rejected + poll).
    attachBlockJobReducers(b, { generateThunk: generateCddBlockThunk, pollThunk: pollCddJobThunk,
                                 resumeThunk: resumeCddJobThunk });
    // Shared archive/restore/purge cases.
    attachArchiveReducers(b, cddArchiveThunks);
  },
});

export const {
  clearError, clearArchiveRefusal, clearGenerating, setActiveCddLocal, resetBlockJob,
} = cddSlice.actions;
export default cddSlice.reducer;

export const selectCdds           = (s) => s.cdd.cdds;
export const selectActiveCdd      = (s) => s.cdd.activeCdd;
export const selectCddVersions    = (s) => s.cdd.versions;
export const selectCddLoading     = (s) => s.cdd.isLoading;
export const selectCddGenerating  = (s) => s.cdd.isGenerating;
export const selectCddError       = (s) => s.cdd.error;
export const selectArchivedCdds   = (s) => s.cdd.archivedCdds;
export const selectCddArchiving   = (s) => s.cdd.isArchiving;
export const selectCddArchiveRefusal = (s) => s.cdd.archiveRefusal;
export const selectCddBlockJob     = (s) => s.cdd.blockJob;
