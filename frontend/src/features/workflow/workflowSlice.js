import { createSlice } from '@reduxjs/toolkit';
import {
  fetchWorkflowBlocksThunk, submitBlockThunk, approveBlockThunk, requestChangesThunk,
  publishBlockThunk, archiveBlockThunk, bulkApproveThunk, fetchPendingReviewsThunk,
} from './workflowThunks';

const initialState = {
  blocks:          [],
  pendingCount:    0,
  filters: {
    status:    '',
    reviewer:  '',
    projectId: null,
    courseId:  null,
    search:    '',
  },
  isLoading:  false,
  error:      null,
};

const workflowSlice = createSlice({
  name: 'workflow',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
    setFilters(s, { payload }) { s.filters = { ...s.filters, ...payload }; },
    resetFilters(s) { s.filters = initialState.filters; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchWorkflowBlocksThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchWorkflowBlocksThunk.fulfilled, (s, { payload }) => { s.isLoading = false; s.blocks = payload; })
      .addCase(fetchWorkflowBlocksThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(fetchPendingReviewsThunk.fulfilled, (s, { payload }) => {
        s.pendingCount = payload?.count ?? (Array.isArray(payload?.blocks) ? payload.blocks.length : 0);
      })
      .addCase(bulkApproveThunk.fulfilled, (s, { payload }) => {
        const approvedIds = new Set(payload.map((b) => b.id));
        s.blocks = s.blocks.map((b) => approvedIds.has(b.id) ? (payload.find((p) => p.id === b.id) || b) : b);
      })
      // All state-transition thunks update the block in-place
      .addMatcher(
        (action) => [
          submitBlockThunk.fulfilled.type,
          approveBlockThunk.fulfilled.type,
          requestChangesThunk.fulfilled.type,
          publishBlockThunk.fulfilled.type,
          archiveBlockThunk.fulfilled.type,
        ].includes(action.type),
        (s, { payload }) => {
          s.blocks = s.blocks.map((b) => b.id === payload.id ? payload : b);
        },
      )
  },
});

export const { clearError, setFilters, resetFilters } = workflowSlice.actions;
export default workflowSlice.reducer;

export const selectWorkflowBlocks  = (s) => s.workflow.blocks;
export const selectWorkflowFilters = (s) => s.workflow.filters;
export const selectPendingCount    = (s) => s.workflow.pendingCount;
export const selectWorkflowLoading = (s) => s.workflow.isLoading;
export const selectWorkflowError   = (s) => s.workflow.error;

// Derived: blocks grouped by workflow state
export const selectBlocksByState = (s) => {
  const rawBlocks = s?.workflow?.blocks;
  const blocks = Array.isArray(rawBlocks)
    ? rawBlocks
    : Array.isArray(rawBlocks?.items)
      ? rawBlocks.items
      : [];

  return blocks.reduce((acc, block) => {
    const state = block.workflow_state || 'draft';
    if (!acc[state]) acc[state] = [];
    acc[state].push(block);
    return acc;
  }, {});
};
