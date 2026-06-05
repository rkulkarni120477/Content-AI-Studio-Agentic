import { createAsyncThunk } from '@reduxjs/toolkit';
import { workflowService } from './services/workflowService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchWorkflowBlocksThunk = createAsyncThunk(
  'workflow/fetchBlocks',
  async (filters, { rejectWithValue }) => {
    try { return await workflowService.listBlocks(filters); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchPendingReviewsThunk = createAsyncThunk(
  'workflow/fetchPending',
  async (_, { rejectWithValue }) => {
    try { return await workflowService.getPending(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const submitBlockThunk = createAsyncThunk(
  'workflow/submit',
  async ({ blockId, reviewer }, { rejectWithValue }) => {
    try {
      const result = await workflowService.submit(blockId, { reviewer_username: reviewer });
      toast.success(`Block #${blockId} submitted for review.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const approveBlockThunk = createAsyncThunk(
  'workflow/approve',
  async ({ blockId, comment }, { rejectWithValue }) => {
    try {
      const result = await workflowService.approve(blockId, { comment: comment || '' });
      toast.success(`Block #${blockId} approved.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const requestChangesThunk = createAsyncThunk(
  'workflow/requestChanges',
  async ({ blockId, reason }, { rejectWithValue }) => {
    try {
      const result = await workflowService.requestChanges(blockId, { reason });
      toast.success(`Changes requested for block #${blockId}.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const rejectBlockThunk = createAsyncThunk(
  'workflow/reject',
  async ({ blockId, reason }, { rejectWithValue }) => {
    try {
      const result = await workflowService.reject(blockId, { reason });
      toast.success(`Block #${blockId} rejected.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const publishBlockThunk = createAsyncThunk(
  'workflow/publish',
  async (blockId, { rejectWithValue }) => {
    try {
      const result = await workflowService.publish(blockId);
      toast.success(`Block #${blockId} published.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const archiveBlockThunk = createAsyncThunk(
  'workflow/archive',
  async (blockId, { rejectWithValue }) => {
    try {
      const result = await workflowService.archive(blockId);
      toast.success(`Block #${blockId} archived.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const resetDraftBlockThunk = createAsyncThunk(
  'workflow/resetDraft',
  async (blockId, { rejectWithValue }) => {
    try {
      const result = await workflowService.resetDraft(blockId);
      toast.success('Block reset to Draft.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const bulkApproveThunk = createAsyncThunk(
  'workflow/bulkApprove',
  async (blockIds, { rejectWithValue }) => {
    try {
      const result = await workflowService.bulkApprove(blockIds);
      toast.success(
        `Approved: ${result.approved?.length ?? 0} | Skipped: ${result.skipped?.length ?? 0}`,
      );
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
