import { createAsyncThunk } from '@reduxjs/toolkit';
import { generateService } from './services/generateService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';
import toast from 'react-hot-toast';

const POLL_INTERVAL_MS = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS) || 2000;

/** Bumped on each new launch or user cancel so in-flight polls stop rescheduling. */
let pollSession = 0;

export function invalidatePollSession() {
  pollSession += 1;
  return pollSession;
}

function currentPollSession() {
  return pollSession;
}

function normalizePollArg(arg) {
  if (typeof arg === 'string') return { jobId: arg, sessionId: pollSession };
  return { jobId: arg.jobId, sessionId: arg.sessionId ?? pollSession };
}

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
  async (arg, { dispatch, rejectWithValue, getState }) => {
    const { jobId, sessionId } = normalizePollArg(arg);

    if (getState().generate.jobStatus === JOB_STATUSES.CANCELLED) {
      return { status: JOB_STATUSES.CANCELLED, job_id: jobId };
    }

    try {
      const status = await generateService.getJobStatus(jobId);

      if (sessionId !== currentPollSession()) {
        return status;
      }
      if (getState().generate.jobStatus === JOB_STATUSES.CANCELLED) {
        return { status: JOB_STATUSES.CANCELLED, job_id: jobId };
      }

      if (!isTerminalJobStatus(status.status)) {
        setTimeout(() => {
          if (sessionId === currentPollSession()) {
            dispatch(pollJobThunk({ jobId, sessionId }));
          }
        }, POLL_INTERVAL_MS);
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
    invalidatePollSession();
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
