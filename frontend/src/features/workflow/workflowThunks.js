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
      toast.success('Submitted for review.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const approveBlockThunk = createAsyncThunk(
  'workflow/approve',
  async ({ blockId, data }, { rejectWithValue }) => {
    try {
      const result = await workflowService.approve(blockId, data);
      toast.success('Block approved.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const requestChangesThunk = createAsyncThunk(
  'workflow/requestChanges',
  async ({ blockId, data }, { rejectWithValue }) => {
    try {
      const result = await workflowService.requestChanges(blockId, data);
      toast.success('Changes requested.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const publishBlockThunk = createAsyncThunk(
  'workflow/publish',
  async (blockId, { rejectWithValue }) => {
    try {
      const result = await workflowService.publish(blockId);
      toast.success('Block published.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const archiveBlockThunk = createAsyncThunk(
  'workflow/archive',
  async (blockId, { rejectWithValue }) => {
    try {
      const result = await workflowService.archive(blockId);
      toast.success('Block archived.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const bulkApproveThunk = createAsyncThunk(
  'workflow/bulkApprove',
  async (blockIds, { rejectWithValue }) => {
    try {
      const result = await workflowService.bulkApprove(blockIds);
      toast.success(`${blockIds.length} block(s) approved.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
