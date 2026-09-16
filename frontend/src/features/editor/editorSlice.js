import { createSlice } from '@reduxjs/toolkit';
import { extractErrorMessage } from '@utils/helpers';
import {
  fetchGenerationsThunk,
  fetchGenerationBlocksThunk,
  fetchCourseBlocksThunk,
  updateBlockThunk,
  regenerateBlockThunk,
  regenerateBlockItemThunk,
  restoreBlockVersionThunk,
  submitBlockThunk,
  validateCourseThunk,
  validateGenerationThunk,
  exportGenerationThunk,
  exportCourseThunk,
  fetchPlagiarismStatusThunk,
  triggerPlagiarismThunk,
} from './editorThunks';

function toErrorString(payload) {
  if (!payload) return 'An unexpected error occurred.';
  if (typeof payload === 'string') return payload;
  return extractErrorMessage(payload);
}

const initialState = {
  generations: [],
  selectedGenerationId: null,
  selectedGeneration: null,
  blocks: [],
  courseBlocks: [],
  blockVersions: [],
  validation: null,
  genValidation: null,
  plagiarismByBlock: {},
  isLoading: false,
  isLoadingBlocks: false,
  isExporting: false,
  isValidating: false,
  isRegenerating: false,
  error: null,
};

const editorSlice = createSlice({
  name: 'editor',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    setSelectedGeneration(s, { payload }) {
      const id = payload?.id ?? payload ?? null;
      s.selectedGenerationId = id;
      s.selectedGeneration = typeof payload === 'object' ? payload : null;
      s.genValidation = null;
      // No selection → no blocks. Clearing here stops a previous title's blocks
      // from lingering when a newly opened title has no generation to select.
      if (!id) s.blocks = [];
    },
    // Full reset of the editor's per-title content — dispatched on a title switch.
    resetEditor() { return initialState; },
    clearValidation(s) { s.validation = null; s.genValidation = null; },
    setPlagiarismReport(s, { payload }) {
      s.plagiarismByBlock[payload.blockId] = payload.report;
    },
    upsertBlock(s, { payload }) {
      s.blocks = s.blocks.map((b) => (b.id === payload.id ? { ...b, ...payload } : b));
    },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchGenerationsThunk.pending, (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchGenerationsThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.generations = payload;
      })
      .addCase(fetchGenerationsThunk.rejected, (s, { payload }) => {
        s.isLoading = false;
        s.error = toErrorString(payload);
      })

      .addCase(fetchGenerationBlocksThunk.pending, (s) => { s.isLoadingBlocks = true; })
      .addCase(fetchGenerationBlocksThunk.fulfilled, (s, { payload }) => {
        s.isLoadingBlocks = false;
        s.blocks = payload;
        if (payload?.length) s.error = null;
      })
      .addCase(fetchGenerationBlocksThunk.rejected, (s) => {
        s.isLoadingBlocks = false;
        s.blocks = [];
      })

      .addCase(fetchCourseBlocksThunk.fulfilled, (s, { payload }) => { s.courseBlocks = payload; })

      .addCase(updateBlockThunk.fulfilled, (s, { payload }) => {
        s.blocks = s.blocks.map((b) => (b.id === payload.id ? payload : b));
      })
      .addCase(regenerateBlockThunk.pending, (s) => { s.isRegenerating = true; })
      .addCase(regenerateBlockThunk.fulfilled, (s, { payload }) => {
        s.isRegenerating = false;
        s.blocks = s.blocks.map((b) => (b.id === payload.block_id ? { ...b, content: payload.content } : b));
      })
      .addCase(regenerateBlockThunk.rejected, (s) => { s.isRegenerating = false; })

      .addCase(regenerateBlockItemThunk.fulfilled, (s, { payload }) => {
        if (payload?.block_id && payload?.updated_content) {
          s.blocks = s.blocks.map((b) => (
            b.id === payload.block_id ? { ...b, content: payload.updated_content } : b
          ));
        }
      })

      .addCase(restoreBlockVersionThunk.fulfilled, (s, { payload }) => {
        s.blocks = s.blocks.map((b) => (b.id === payload.block_id ? { ...b, content: payload.content } : b));
      })

      .addCase(submitBlockThunk.fulfilled, (s, { payload }) => {
        const blockId = payload?.block_id ?? payload?.id;
        const state = payload?.workflow_state;
        if (blockId && state) {
          s.blocks = s.blocks.map((b) => (
            b.id === blockId ? { ...b, workflow_state: state } : b
          ));
        } else if (payload?.id) {
          s.blocks = s.blocks.map((b) => (b.id === payload.id ? { ...b, ...payload } : b));
        }
      })

      .addCase(exportGenerationThunk.pending, (s) => { s.isExporting = true; })
      .addCase(exportGenerationThunk.fulfilled, (s) => { s.isExporting = false; })
      .addCase(exportGenerationThunk.rejected, (s) => { s.isExporting = false; })
      .addCase(exportCourseThunk.pending, (s) => { s.isExporting = true; })
      .addCase(exportCourseThunk.fulfilled, (s) => { s.isExporting = false; })
      .addCase(exportCourseThunk.rejected, (s) => { s.isExporting = false; })

      .addCase(validateCourseThunk.pending, (s) => { s.isValidating = true; s.validation = null; })
      .addCase(validateCourseThunk.fulfilled, (s, { payload }) => { s.isValidating = false; s.validation = payload; })
      .addCase(validateCourseThunk.rejected, (s) => { s.isValidating = false; })

      .addCase(validateGenerationThunk.pending, (s) => { s.isValidating = true; })
      .addCase(validateGenerationThunk.fulfilled, (s, { payload }) => { s.isValidating = false; s.genValidation = payload; })
      .addCase(validateGenerationThunk.rejected, (s) => { s.isValidating = false; })

      .addCase(triggerPlagiarismThunk.fulfilled, (s, { payload, meta }) => {
        const blockId = meta.arg;
        s.plagiarismByBlock[blockId] = { ...payload, status: 'pending' };
      })
      .addCase(fetchPlagiarismStatusThunk.fulfilled, (s, { payload, meta }) => {
        const { blockId } = meta.arg;
        s.plagiarismByBlock[blockId] = payload;
      });
  },
});

export const {
  clearError, setSelectedGeneration, clearValidation, setPlagiarismReport, upsertBlock, resetEditor,
} = editorSlice.actions;
export default editorSlice.reducer;

export const selectGenerations = (s) => s.editor.generations;
export const selectSelectedGenerationId = (s) => s.editor.selectedGenerationId;
export const selectSelectedGeneration = (s) => s.editor.selectedGeneration;
export const selectBlocks = (s) => s.editor.blocks;
export const selectCourseBlocks = (s) => s.editor.courseBlocks;
export const selectBlockVersions = (s) => s.editor.blockVersions;
export const selectValidation = (s) => s.editor.validation;
export const selectGenValidation = (s) => s.editor.genValidation;
export const selectPlagiarismByBlock = (s) => s.editor.plagiarismByBlock;
export const selectEditorLoading = (s) => s.editor.isLoading;
export const selectEditorLoadingBlocks = (s) => s.editor.isLoadingBlocks;
export const selectEditorExporting = (s) => s.editor.isExporting;
export const selectEditorValidating = (s) => s.editor.isValidating;
export const selectEditorRegenerating = (s) => s.editor.isRegenerating;
export const selectEditorError = (s) => s.editor.error;
