import { createAsyncThunk } from '@reduxjs/toolkit';
import { feedbackService } from './services/feedbackService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchFeedbackThunk = createAsyncThunk(
  'feedback/fetch',
  async (courseId, { rejectWithValue }) => {
    try {
      return await feedbackService.listFeedback(Number(courseId));
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const analyzeFeedbackThunk = createAsyncThunk(
  'feedback/analyze',
  async ({ file, courseId, onProgress }, { rejectWithValue }) => {
    try {
      const result = await feedbackService.analyze(file, Number(courseId), onProgress);
      const count = result?.items?.length ?? 0;
      toast.success(
        count > 0
          ? `Extracted ${count} feedback item${count === 1 ? '' : 's'}.`
          : 'No reviewer feedback was found in that document.',
      );
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const recommendFeedbackThunk = createAsyncThunk(
  'feedback/recommend',
  async ({ itemIds, guidance, modelChoice } = {}, { rejectWithValue }) => {
    try {
      const ids = (itemIds || []).map(Number);
      const result = await feedbackService.recommend(ids, { guidance, modelChoice });
      const ok = result?.recommended ?? 0;
      const failed = result?.failed ?? 0;
      if (ok > 0) {
        toast.success(
          `Generated ${ok} recommendation${ok === 1 ? '' : 's'}`
          + (failed > 0 ? ` · ${failed} failed.` : '.'),
        );
      } else {
        toast.error('Could not generate a recommendation. Please try again.');
      }
      return { items: result?.items ?? [], requestedIds: ids };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const deleteFeedbackItemThunk = createAsyncThunk(
  'feedback/deleteItem',
  async (itemId, { rejectWithValue }) => {
    try {
      await feedbackService.deleteItem(itemId);
      toast.success('Feedback item deleted.');
      return { id: itemId };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const bulkDeleteFeedbackThunk = createAsyncThunk(
  'feedback/bulkDelete',
  async (ids, { rejectWithValue }) => {
    try {
      await feedbackService.bulkDelete(ids);
      toast.success(`Deleted ${ids.length} feedback item${ids.length === 1 ? '' : 's'}.`);
      return { ids };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
