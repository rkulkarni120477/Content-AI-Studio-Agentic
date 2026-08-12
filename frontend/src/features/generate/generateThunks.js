import { createAsyncThunk } from '@reduxjs/toolkit';
import { generateService } from './services/generateService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { JOB_STATUSES } from '@utils/constants';
import toast from 'react-hot-toast';

const POLL_INTERVAL_MS = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS) || 2000;

export const launchGenerationThunk = createAsyncThunk(
  'generate/launch',
  async (payload, { rejectWithValue }) => {
    try {
      if (!payload?.project_id) {
        return rejectWithValue('Select a project before launching generation.');
      }
      const accepted = await generateService.launch(payload);
      return accepted;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const pollJobThunk = createAsyncThunk(
  'generate/pollJob',
  async (jobId, { dispatch, rejectWithValue }) => {
    try {
      const status = await generateService.getJobStatus(jobId);
      const active = ['pending', 'queued', 'running'].includes(status.status);
      if (active) {
        setTimeout(() => dispatch(pollJobThunk(jobId)), POLL_INTERVAL_MS);
        return status;
      }
      if (status.status === JOB_STATUSES.COMPLETED) {
        const usageMsg = formatUsageSummaryMessage(status.usage_summary);
        const message = usageMsg ? `Generation completed! ${usageMsg}` : 'Generation completed!';
        if (hasOverBudget(status.usage_summary)) toast.error(message); else toast.success(message);
        let blocks = [];
        if (status.generation_id) {
          try {
            const listItems = await generateService.getGenerationBlocks(status.generation_id);
            if (listItems.length > 0) {
              blocks = await Promise.all(
                listItems.map(async (item) => {
                  try {
                    return await generateService.getBlock(item.id);
                  } catch {
                    return item;
                  }
                }),
              );
            }
          } catch {
            try {
              const gen = await generateService.getGeneration(status.generation_id);
              blocks = gen.blocks || [];
            } catch {
              // block fetch failed silently — content is available in Editor tab
            }
          }
        }
        return { ...status, blocks };
      }
      if (status.status === JOB_STATUSES.FAILED) {
        toast.error(status.error_message || 'Generation failed.');
      }
      return status;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const cancelJobThunk = createAsyncThunk(
  'generate/cancelJob',
  async (jobId, { rejectWithValue }) => {
    try {
      const result = await generateService.cancelJob(jobId);
      toast.success('Generation job cancelled.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** @deprecated use launchGenerationThunk */
export const runGenerationThunk = launchGenerationThunk;
export const queueGenerationThunk = launchGenerationThunk;
