import { createSlice } from '@reduxjs/toolkit';
import {
  fetchPromptsThunk, commitPromptThunk,
  fetchPromptVersionsThunk, aiGeneratePromptThunk,
} from './promptsThunks';

const initialState = {
  prompts:  [],
  versions: [],
  aiGenerated: null,
  isLoading:   false,
  isAiGenerating: false,
  error: null,
  // requestId of the most recently DISPATCHED prompts fetch. Same race this
  // codebase already guards against for clusters/courses (see dashboardSlice):
  // switching the selected tenant re-dispatches this fetch with a new
  // project_id, but a slower response for the PREVIOUS tenant can still land
  // after the new tenant's faster one — without this guard it would clobber
  // the dropdown with the wrong tenant's prompts (filteredPrompts in
  // InlinePromptControls only filters by component_type, not project_id, so
  // there's no client-side net to catch it).
  promptsRequestId: null,
};

const promptsSlice = createSlice({
  name: 'prompts',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    clearAiGenerated(s) { s.aiGenerated = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchPromptsThunk.pending,   (s, action) => {
        s.promptsRequestId = action.meta.requestId;
        s.isLoading = true;
        s.error = null;
      })
      .addCase(fetchPromptsThunk.fulfilled, (s, action) => {
        if (action.meta.requestId !== s.promptsRequestId) return;
        s.isLoading = false;
        s.prompts = action.payload;
      })
      .addCase(fetchPromptsThunk.rejected,  (s, action) => {
        if (action.meta.requestId !== s.promptsRequestId) return;
        s.isLoading = false;
        s.error = action.payload;
      })

      .addCase(commitPromptThunk.fulfilled, (s, { payload }) => {
        const exists = s.prompts.find((p) => p.name === payload.name);
        if (exists) {
          s.prompts = s.prompts.map((p) => p.name === payload.name ? payload : p);
        } else {
          s.prompts.unshift(payload);
        }
      })

      .addCase(fetchPromptVersionsThunk.fulfilled, (s, { payload }) => { s.versions = payload; })

      .addCase(aiGeneratePromptThunk.pending,   (s) => { s.isAiGenerating = true; s.aiGenerated = null; })
      .addCase(aiGeneratePromptThunk.fulfilled, (s, { payload }) => { s.isAiGenerating = false; s.aiGenerated = payload; })
      .addCase(aiGeneratePromptThunk.rejected,  (s, { payload }) => { s.isAiGenerating = false; s.error = payload; });
  },
});

export const { clearError, clearAiGenerated } = promptsSlice.actions;
export default promptsSlice.reducer;

export const selectPrompts         = (s) => s.prompts.prompts;
export const selectPromptVersions  = (s) => s.prompts.versions;
export const selectAiGenerated     = (s) => s.prompts.aiGenerated;
export const selectPromptsLoading  = (s) => s.prompts.isLoading;
export const selectAiGenerating    = (s) => s.prompts.isAiGenerating;
export const selectPromptsError    = (s) => s.prompts.error;
