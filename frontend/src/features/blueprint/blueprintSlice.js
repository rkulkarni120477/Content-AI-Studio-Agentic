import { createSlice } from '@reduxjs/toolkit';
import { attachBlockJobReducers } from '@features/shared/blockJob';
import { attachArchiveReducers } from '@features/shared/documentArchive';
import {
  fetchBlueprintsThunk, generateBlueprintThunk, importBlueprintThunk, setActiveBlueprintThunk,
  importBlueprintAsyncThunk, pollOutlineImportJobThunk, resumeOutlineImportJobThunk,
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
  // In-flight async Outline import job ({ jobId, status, progress, currentStep }),
  // or null. Drives the "processing…" indicator and survives a page refresh.
  importJob:      null,
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
    // Drop the per-title blueprint context (active blueprint + its parsed
    // components + versions) on a title switch, so one title's blueprint can't
    // render under another before the new title's fetch resolves.
    resetBlueprintContext(s) {
      s.activeBlueprint = null;
      s.components = [];
      s.versions = [];
    },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchBlueprintsThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchBlueprintsThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.blueprints = payload?.items ?? payload ?? [];
        // Reset the active blueprint to whatever THIS course actually has — mirror
        // cddSlice's `?? null`. Previously this only assigned when truthy, so a
        // title with no blueprint kept the PREVIOUS title's active blueprint and
        // its parsed components, leaking another title's lessons into the Generate
        // tab's Content-Type dropdown. When there's no active blueprint the parsed
        // components are stale too, so drop them.
        s.activeBlueprint = payload?.activeBlueprint ?? null;
        if (!s.activeBlueprint) s.components = [];
      })
      .addCase(fetchBlueprintsThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(generateBlueprintThunk.pending,   (s) => { s.isGenerating = true; s.error = null; })
      .addCase(generateBlueprintThunk.fulfilled, (s, { payload }) => {
        s.isGenerating = false;
        // Async enqueue — list/active refresh when JobTracker completes the job.
        if (payload?.job_id) return;
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

      // Async import (timeout-proof): enqueue → poll. `isImporting` stays true from
      // enqueue until the poll reaches a terminal state, driving the processing UI.
      .addCase(importBlueprintAsyncThunk.pending,   (s) => {
        s.isImporting = true; s.error = null;
        s.importJob = { jobId: null, status: 'queued', progress: 0, currentStep: null };
      })
      .addCase(importBlueprintAsyncThunk.fulfilled, (s, { payload }) => {
        s.importJob = { jobId: payload?.job_id ?? null, status: payload?.status || 'queued', progress: 0, currentStep: null };
      })
      .addCase(importBlueprintAsyncThunk.rejected,  (s, { payload }) => {
        s.isImporting = false; s.importJob = null; s.error = payload;
      })
      .addCase(resumeOutlineImportJobThunk.fulfilled, (s, { payload }) => {
        if (payload?.job_id) {
          s.isImporting = true;
          s.importJob = { jobId: payload.job_id, status: payload.status || 'running', progress: payload.progress ?? 0, currentStep: payload.current_step ?? null };
        }
      })
      .addCase(pollOutlineImportJobThunk.fulfilled, (s, { payload }) => {
        if (!payload || payload.transientError) return;   // keep prior state on a retried tick
        const status = String(payload.status || '').toLowerCase();
        s.importJob = {
          jobId: payload.job_id ?? s.importJob?.jobId ?? null,
          status,
          progress: payload.progress ?? s.importJob?.progress ?? 0,
          currentStep: payload.current_step ?? s.importJob?.currentStep ?? null,
        };
        if (['completed', 'failed', 'cancelled'].includes(status)) {
          s.isImporting = false;
          s.importJob = null;
          // On success, drop the imported/updated Outline in and make it active —
          // same landing as a generate. The list refetch in the thunk also runs.
          if (status === 'completed' && payload.blueprint?.id) {
            const bp = payload.blueprint;
            const idx = s.blueprints.findIndex((b) => b.id === bp.id);
            if (idx >= 0) s.blueprints[idx] = { ...s.blueprints[idx], ...bp };
            else s.blueprints.unshift(bp);
            s.activeBlueprint = bp;
          }
        }
      })
      .addCase(pollOutlineImportJobThunk.rejected, (s) => {
        // Lost contact with the poll endpoint — stop the spinner; the job may still
        // finish server-side and a page reload will reattach to it.
        s.isImporting = false; s.importJob = null;
      })

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
  resetBlueprintContext,
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
export const selectBlueprintImportJob  = (s) => s.blueprint.importJob;
export const selectBlueprintError      = (s) => s.blueprint.error;
export const selectBlueprintBlockJob   = (s) => s.blueprint.blockJob;
export const selectArchivedBlueprints  = (s) => s.blueprint.archivedBlueprints;
export const selectBlueprintArchiving  = (s) => s.blueprint.isArchiving;
export const selectBlueprintArchiveRefusal = (s) => s.blueprint.archiveRefusal;
