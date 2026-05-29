import { createSlice } from '@reduxjs/toolkit';
import {
  fetchBlueprintsThunk, generateBlueprintThunk, setActiveBlueprintThunk,
  fetchBlueprintVersionsThunk, commitBlueprintVersionThunk, fetchBlueprintComponentsThunk,
} from './blueprintThunks';

const initialState = {
  blueprints:     [],
  activeBlueprint: null,
  components:     [],  // parsed blueprint components for the generate step
  versions:       [],
  generationMode: 'student',
  isLoading:      false,
  isGenerating:   false,
  error:          null,
};

const blueprintSlice = createSlice({
  name: 'blueprint',
  initialState,
  reducers: {
    clearError(s)                     { s.error = null; },
    setGenerationMode(s, { payload }) { s.generationMode = payload; },
    setActiveBlueprintLocal(s, { payload }) { s.activeBlueprint = payload; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchBlueprintsThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchBlueprintsThunk.fulfilled, (s, { payload }) => { s.isLoading = false; s.blueprints = payload; })
      .addCase(fetchBlueprintsThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(generateBlueprintThunk.pending,   (s) => { s.isGenerating = true; s.error = null; })
      .addCase(generateBlueprintThunk.fulfilled, (s, { payload }) => {
        s.isGenerating   = false;
        s.blueprints.unshift(payload);
        s.activeBlueprint = payload;
      })
      .addCase(generateBlueprintThunk.rejected,  (s, { payload }) => { s.isGenerating = false; s.error = payload; })

      .addCase(setActiveBlueprintThunk.fulfilled, (s, { payload }) => { s.activeBlueprint = payload; })

      .addCase(fetchBlueprintVersionsThunk.fulfilled, (s, { payload }) => { s.versions = payload; })
      .addCase(commitBlueprintVersionThunk.fulfilled, (s, { payload }) => { s.versions.unshift(payload); })

      .addCase(fetchBlueprintComponentsThunk.fulfilled, (s, { payload }) => { s.components = payload; });
  },
});

export const { clearError, setGenerationMode, setActiveBlueprintLocal } = blueprintSlice.actions;
export default blueprintSlice.reducer;

export const selectBlueprints          = (s) => s.blueprint.blueprints;
export const selectActiveBlueprint     = (s) => s.blueprint.activeBlueprint;
export const selectBlueprintComponents = (s) => s.blueprint.components;
export const selectBlueprintVersions   = (s) => s.blueprint.versions;
export const selectBlueprintGenerationMode = (s) => s.blueprint.generationMode;
export const selectBlueprintLoading    = (s) => s.blueprint.isLoading;
export const selectBlueprintGenerating = (s) => s.blueprint.isGenerating;
export const selectBlueprintError      = (s) => s.blueprint.error;
