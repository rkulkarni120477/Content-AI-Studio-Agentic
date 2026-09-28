import { createAsyncThunk } from '@reduxjs/toolkit';
import { reviewService } from './services/reviewService';
import { extractErrorMessage } from '@utils/helpers';

const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 150;          // ~5 min ceiling
const MAX_POLL_ERRORS = 5;

/** Fetch the active checklist for a project (null if none). */
export const fetchActiveChecklistThunk = createAsyncThunk(
  'review/fetchActive',
  async (projectId = null, { rejectWithValue }) => {
    try {
      return await reviewService.getActiveChecklist(projectId);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/**
 * Upload a checklist file, poll the split job to completion, then return the
 * freshly-built active checklist.
 */
export const uploadChecklistThunk = createAsyncThunk(
  'review/upload',
  async ({ file, name, modelChoice, projectId, onProgress }, { rejectWithValue }) => {
    try {
      const accepted = await reviewService.uploadChecklist(file, { name, modelChoice, projectId }, onProgress);
      const jobId = accepted?.job_id;
      if (!jobId) {
        // No async handle — treat the response as the final checklist if shaped so.
        return accepted?.id ? accepted : await reviewService.getActiveChecklist(projectId);
      }

      let pollErrors = 0;
      for (let i = 0; i < MAX_POLLS; i += 1) {
        let status;
        try {
          status = await reviewService.getJobStatus(jobId);
          pollErrors = 0;
        } catch (e) {
          pollErrors += 1;
          if (pollErrors >= MAX_POLL_ERRORS) throw e;
        }
        if (status?.status === 'completed') {
          return await reviewService.getActiveChecklist(projectId);
        }
        if (status?.status === 'failed' || status?.status === 'cancelled') {
          return rejectWithValue(
            status.error_message || 'The checklist could not be processed. Please try again.',
          );
        }
        await new Promise((resolve) => { setTimeout(resolve, POLL_INTERVAL_MS); });
      }
      return rejectWithValue(
        'Splitting the checklist is taking longer than expected. It may still finish — reopen the panel to check.',
      );
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Edit one rule (any subset of fields). Returns the updated rule. */
export const updateChecklistItemThunk = createAsyncThunk(
  'review/updateItem',
  async ({ checklistId, itemId, patch }, { rejectWithValue }) => {
    try {
      return await reviewService.updateItem(checklistId, itemId, patch);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Reorder a checklist's rules by id. Returns the updated checklist. */
export const reorderChecklistThunk = createAsyncThunk(
  'review/reorder',
  async ({ checklistId, itemIds }, { rejectWithValue }) => {
    try {
      return await reviewService.reorder(checklistId, itemIds);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Delete one or more rules (hard). Returns the removed ids for slice pruning. */
export const deleteChecklistItemsThunk = createAsyncThunk(
  'review/deleteItems',
  async ({ checklistId, itemIds }, { rejectWithValue }) => {
    try {
      if (itemIds.length === 1) await reviewService.deleteItem(checklistId, itemIds[0]);
      else await reviewService.bulkDeleteItems(checklistId, itemIds);
      return { itemIds };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Delete the whole checklist (hard). */
export const deleteChecklistThunk = createAsyncThunk(
  'review/deleteChecklist',
  async (checklistId, { rejectWithValue }) => {
    try {
      await reviewService.deleteChecklist(checklistId);
      return true;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Fetch the non-pass checklist results for a review. */
export const fetchChecklistResultsThunk = createAsyncThunk(
  'review/checklistResults',
  async (reviewId, { rejectWithValue }) => {
    try {
      const rows = await reviewService.getChecklistResults(reviewId);
      return Array.isArray(rows) ? rows : (rows?.items ?? []);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Fetch detected issues for a review. */
export const fetchFindingsThunk = createAsyncThunk(
  'review/findings',
  async (reviewId, { rejectWithValue }) => {
    try {
      const rows = await reviewService.getFindings(reviewId);
      return Array.isArray(rows) ? rows : (rows?.items ?? []);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Apply one finding's fix. Returns the updated finding. */
export const applyFindingThunk = createAsyncThunk(
  'review/applyFinding',
  async ({ reviewId, findingId }, { rejectWithValue }) => {
    try {
      return await reviewService.applyFinding(reviewId, findingId);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Apply selected findings, or all eligible (null). Returns the batch summary. */
export const applyFindingsThunk = createAsyncThunk(
  'review/applyFindings',
  async ({ reviewId, findingIds = null }, { rejectWithValue }) => {
    try {
      return await reviewService.applyFindings(reviewId, findingIds);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Dismiss a finding. Returns the updated finding. */
export const dismissFindingThunk = createAsyncThunk(
  'review/dismissFinding',
  async ({ reviewId, findingId, reason }, { rejectWithValue }) => {
    try {
      return await reviewService.dismissFinding(reviewId, findingId, reason);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Fetch the latest review for a generation (for showing prior state on open). */
export const fetchLatestReviewThunk = createAsyncThunk(
  'review/fetchLatest',
  async (generationId, { rejectWithValue }) => {
    try {
      const rows = await reviewService.listReviews(generationId, 1);
      const list = Array.isArray(rows) ? rows : (rows?.items ?? []);
      return list[0] || null;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Fetch a generation's review history (newest first). */
export const fetchReviewHistoryThunk = createAsyncThunk(
  'review/history',
  async (generationId, { rejectWithValue }) => {
    try {
      const rows = await reviewService.listReviews(generationId, 20);
      return Array.isArray(rows) ? rows : (rows?.items ?? []);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/**
 * Start (or reuse) a review of a generation, then poll the job to completion and
 * return the final review record.
 */
export const startReviewThunk = createAsyncThunk(
  'review/start',
  async ({ generationId, modelChoice }, { rejectWithValue }) => {
    try {
      const accepted = await reviewService.startReview(generationId, modelChoice);
      const reviewId = accepted?.review_id;
      const reused = Boolean(accepted?.reused);

      // Identical completed run returned — no job to poll.
      if (reused || !accepted?.job_id) {
        const review = reviewId ? await reviewService.getReview(reviewId) : null;
        return { review, reused };
      }

      const jobId = accepted.job_id;
      let pollErrors = 0;
      for (let i = 0; i < MAX_POLLS; i += 1) {
        let status;
        try {
          status = await reviewService.getJobStatus(jobId);
          pollErrors = 0;
        } catch (e) {
          pollErrors += 1;
          if (pollErrors >= MAX_POLL_ERRORS) throw e;
        }
        if (status?.status === 'completed') {
          return { review: await reviewService.getReview(reviewId), reused: false };
        }
        if (status?.status === 'failed' || status?.status === 'cancelled') {
          return rejectWithValue(status.error_message || 'The review could not be completed.');
        }
        await new Promise((resolve) => { setTimeout(resolve, POLL_INTERVAL_MS); });
      }
      return rejectWithValue(
        'The review is taking longer than expected. It may still finish — reopen the panel to check.',
      );
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
