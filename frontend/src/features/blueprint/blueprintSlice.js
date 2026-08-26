import { createSlice } from '@reduxjs/toolkit';
import { attachBlockJobReducers } from '@features/shared/blockJob';
import { attachArchiveReducers } from '@features/shared/documentArchive';
import {
  fetchBlueprintsThunk, generateBlueprintThunk, importBlueprintThunk, setActiveBlueprintThunk,
  fetchBlueprintVersionsThunk, commitBlueprintVersionThunk, fetchBlueprintComponentsThunk,
  activateBlueprintVersionThunk, generateBlueprintBlockThunk, pollBlueprintJobThunk, resumeBlueprintJobThunk,
  fetchArchivedBlueprintsThunk, blueprintArchiveThunks,
} from './blueprintThunks';

const initialState = {
  blueprints:     [],
  activeBlueprint: null,
  components:     [],  // parsed blueprint components for the generate step
  versions:       [],
  generationMode: 'student',
  isLoading:      false,
  isGenerating:   false,
  isImporting:    false,
  error:          null,
  // Block-wide async job tracking (digest pipeline).
  blockJob:       null,  // { jobId, status, progress, currentStep }
  // Archive. Held apart from `blueprints` because that array feeds the module
  // picker — a retired document must not be one missed filter away from a prompt.
  archivedBlueprints: [],
  isArchiving:        false,
  // A refused archive/purge, with the server's reasons. Separate from `error`
  // so the list stays on screen while the user acts on the refusal.
  archiveRefusal:     null,
};

const blueprintSlice = createSlice({
  name: 'blueprint',
  initialState,
  reducers: {
    clearError(s)                     { s.error = null; },
    clearArchiveRefusal(s)            { s.archiveRefusal = null; },
    setGenerationMode(s, { payload }) { s.generationMode = payload; },
    setActiveBlueprintLocal(s, { payload }) { s.activeBlueprint = payload; },
    resetBlockJob(s) { s.blockJob = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchBlueprintsThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchBlueprintsThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.blueprints = payload?.items ?? payload ?? [];
        if (payload?.activeBlueprint) s.activeBlueprint = payload.activeBlueprint;
      })
      .addCase(fetchBlueprintsThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(generateBlueprintThunk.pending,   (s) => { s.isGenerating = true; s.error = null; })
      .addCase(generateBlueprintThunk.fulfilled, (s, { payload }) => {
        s.isGenerating = false;
        if (payload?.id) {
          const idx = s.blueprints.findIndex((b) => b.id === payload.id);
          if (idx >= 0) s.blueprints[idx] = { ...s.blueprints[idx], ...payload };
          else s.blueprints.unshift(payload);
          s.activeBlueprint = payload;
        }
      })
      .addCase(generateBlueprintThunk.rejected,  (s, { payload }) => {
        s.isGenerating = false;
        s.error = payload;
      })

      // Import lands where a generate does: the imported/updated Outline becomes
      // the selected + active one. It may be a brand-new row or a new version of
      // an existing day's Outline, so upsert by id rather than always prepending.
      .addCase(importBlueprintThunk.pending,   (s) => { s.isImporting = true; s.error = null; })
      .addCase(importBlueprintThunk.fulfilled, (s, { payload }) => {
        s.isImporting = false;
        if (payload?.id) {
          const idx = s.blueprints.findIndex((b) => b.id === payload.id);
          if (idx >= 0) s.blueprints[idx] = { ...s.blueprints[idx], ...payload };
          else s.blueprints.unshift(payload);
          s.activeBlueprint = payload;
        }
      })
      .addCase(importBlueprintThunk.rejected,  (s, { payload }) => { s.isImporting = false; s.error = payload; })

      .addCase(setActiveBlueprintThunk.fulfilled, (s, { payload }) => { s.activeBlueprint = payload; })

      .addCase(fetchBlueprintVersionsThunk.fulfilled, (s, { payload }) => { s.versions = payload; })
      .addCase(commitBlueprintVersionThunk.fulfilled, (s, { payload }) => { s.versions.unshift(payload); })
      .addCase(activateBlueprintVersionThunk.fulfilled, (s, { payload }) => {
        if (s.activeBlueprint?.id === payload?.id) s.activeBlueprint = payload;
      })

      .addCase(fetchBlueprintComponentsThunk.fulfilled, (s, { payload }) => {
        s.components = Array.isArray(payload) ? payload : (payload?.components || []);
      });

    b.addCase(fetchArchivedBlueprintsThunk.fulfilled, (s, { payload }) => {
      s.archivedBlueprints = payload ?? [];
    });
    // A failed archived-list load leaves the previous contents rather than
    // emptying them — "none archived" and "we could not check" must not look
    // the same right before someone decides to regenerate.

    // Shared block-wide async-job cases (pending/fulfilled/rejected + poll).
    attachBlockJobReducers(b, { generateThunk: generateBlueprintBlockThunk, pollThunk: pollBlueprintJobThunk,
                                 resumeThunk: resumeBlueprintJobThunk });
    // Shared archive/restore/purge cases.
    attachArchiveReducers(b, blueprintArchiveThunks);
  },
});

export const {
  clearError, clearArchiveRefusal, setGenerationMode, setActiveBlueprintLocal, resetBlockJob,
} = blueprintSlice.actions;
export default blueprintSlice.reducer;

export const selectBlueprints          = (s) => s.blueprint.blueprints;
export const selectActiveBlueprint     = (s) => s.blueprint.activeBlueprint;
export const selectBlueprintComponents = (s) => s.blueprint.components;
export const selectBlueprintVersions   = (s) => s.blueprint.versions;
export const selectBlueprintGenerationMode = (s) => s.blueprint.generationMode;
export const selectBlueprintLoading    = (s) => s.blueprint.isLoading;
export const selectBlueprintGenerating = (s) => s.blueprint.isGenerating;
export const selectBlueprintImporting  = (s) => s.blueprint.isImporting;
export const selectBlueprintError      = (s) => s.blueprint.error;
export const selectBlueprintBlockJob   = (s) => s.blueprint.blockJob;
export const selectArchivedBlueprints  = (s) => s.blueprint.archivedBlueprints;
export const selectBlueprintArchiving  = (s) => s.blueprint.isArchiving;
export const selectBlueprintArchiveRefusal = (s) => s.blueprint.archiveRefusal;
