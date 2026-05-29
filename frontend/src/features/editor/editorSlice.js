import { createSlice } from '@reduxjs/toolkit';
import {
  fetchBlocksThunk, updateBlockThunk,
  fetchBlockVersionsThunk, restoreBlockVersionThunk,
  submitBlockThunk, exportBlockThunk, exportCourseThunk,
  triggerPlagiarismThunk, validateCourseThunk,
} from './editorThunks';

const initialState = {
  blocks:       [],
  selectedBlock: null,
  blockVersions: [],
  validation:    null,   // { passed, issues: [...] }
  isLoading:     false,
  isExporting:   false,
  isValidating:  false,
  error:         null,
};

const editorSlice = createSlice({
  name: 'editor',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    selectBlock(s, { payload }) { s.selectedBlock = payload; },
    clearSelectedBlock(s) { s.selectedBlock = null; s.blockVersions = []; },
    clearValidation(s) { s.validation = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchBlocksThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchBlocksThunk.fulfilled, (s, { payload }) => { s.isLoading = false; s.blocks = payload; })
      .addCase(fetchBlocksThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(updateBlockThunk.fulfilled, (s, { payload }) => {
        s.blocks = s.blocks.map((b) => b.id === payload.id ? payload : b);
        if (s.selectedBlock?.id === payload.id) s.selectedBlock = payload;
      })

      .addCase(fetchBlockVersionsThunk.fulfilled, (s, { payload }) => { s.blockVersions = payload; })

      .addCase(restoreBlockVersionThunk.fulfilled, (s, { payload }) => {
        s.blocks = s.blocks.map((b) => b.id === payload.id ? payload : b);
        s.selectedBlock = payload;
      })

      .addCase(submitBlockThunk.fulfilled, (s, { payload }) => {
        s.blocks = s.blocks.map((b) => b.id === payload.id ? payload : b);
        if (s.selectedBlock?.id === payload.id) s.selectedBlock = payload;
      })

      .addCase(exportCourseThunk.pending,   (s) => { s.isExporting = true; })
      .addCase(exportCourseThunk.fulfilled, (s) => { s.isExporting = false; })
      .addCase(exportCourseThunk.rejected,  (s) => { s.isExporting = false; })

      .addCase(validateCourseThunk.pending,   (s) => { s.isValidating = true; s.validation = null; })
      .addCase(validateCourseThunk.fulfilled, (s, { payload }) => { s.isValidating = false; s.validation = payload; })
      .addCase(validateCourseThunk.rejected,  (s, { payload }) => { s.isValidating = false; s.error = payload; });
  },
});

export const { clearError, selectBlock, clearSelectedBlock, clearValidation } = editorSlice.actions;
export default editorSlice.reducer;

export const selectBlocks        = (s) => s.editor.blocks;
export const selectSelectedBlock = (s) => s.editor.selectedBlock;
export const selectBlockVersions = (s) => s.editor.blockVersions;
export const selectValidation    = (s) => s.editor.validation;
export const selectEditorLoading = (s) => s.editor.isLoading;
export const selectEditorExporting = (s) => s.editor.isExporting;
export const selectEditorValidating = (s) => s.editor.isValidating;
export const selectEditorError   = (s) => s.editor.error;
