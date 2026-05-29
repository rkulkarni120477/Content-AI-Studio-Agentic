import { createAsyncThunk } from '@reduxjs/toolkit';
import { centralService } from './services/centralService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchCentralItemsThunk = createAsyncThunk(
  'central/fetch',
  async (params, { rejectWithValue }) => {
    try { return await centralService.listItems(params); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const createCentralItemThunk = createAsyncThunk(
  'central/create',
  async (data, { rejectWithValue }) => {
    try {
      const result = await centralService.createItem(data);
      toast.success('Item added to Central Repository.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const importFromRegistryThunk = createAsyncThunk(
  'central/import',
  async (data, { rejectWithValue }) => {
    try {
      const result = await centralService.importFromRegistry(data);
      toast.success('Prompt imported to Central Repository.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
