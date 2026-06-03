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
      // Backend does not expose an import endpoint in FastAPI (Streamlit-only feature).
      // Keep thunk but fail gracefully with a clear message.
      return rejectWithValue('Import from Registry is not available in the API.');
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
