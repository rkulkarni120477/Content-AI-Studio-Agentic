import { createSlice } from '@reduxjs/toolkit';
import {
  fetchCddsThunk, generateCddThunk, setActiveCddThunk,
  fetchCddVersionsThunk, commitCddVersionThunk,
} from './cddThunks';

const initialState = {
  cdds:       [],
  activeCdd:  null,  // full CDD object with active version
  versions:   [],
  isLoading:  false,
  isGenerating: false,
  error:      null,
};

const cddSlice = createSlice({
  name: 'cdd',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    clearGenerating(s) { s.isGenerating = false; },
    setActiveCddLocal(s, { payload }) { s.activeCdd = payload; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchCddsThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchCddsThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.cdds = payload?.items ?? payload ?? [];
        if (payload?.activeCdd) s.activeCdd = payload.activeCdd;
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
      });
  },
});

export const { clearError, clearGenerating, setActiveCddLocal } = cddSlice.actions;
export default cddSlice.reducer;

export const selectCdds           = (s) => s.cdd.cdds;
export const selectActiveCdd      = (s) => s.cdd.activeCdd;
export const selectCddVersions    = (s) => s.cdd.versions;
export const selectCddLoading     = (s) => s.cdd.isLoading;
export const selectCddGenerating  = (s) => s.cdd.isGenerating;
export const selectCddError       = (s) => s.cdd.error;
