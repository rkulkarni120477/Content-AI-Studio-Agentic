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
      .addCase(fetchPromptsThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchPromptsThunk.fulfilled, (s, { payload }) => { s.isLoading = false; s.prompts = payload; })
      .addCase(fetchPromptsThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

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
