import { createAsyncThunk } from '@reduxjs/toolkit';
import { styleService } from './services/styleService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchStylesThunk = createAsyncThunk(
  'style/fetchStyles',
  async (_, { rejectWithValue }) => {
    try { return await styleService.listStyles(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const createStyleThunk = createAsyncThunk(
  'style/create',
  async (data, { rejectWithValue }) => {
    try {
      const result = await styleService.createStyle(data);
      toast.success('Style created and AI understanding generated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const activateStyleThunk = createAsyncThunk(
  'style/activate',
  async (styleId, { rejectWithValue }) => {
    try {
      const result = await styleService.activateStyle(styleId);
      toast.success('Style activated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const deactivateStyleThunk = createAsyncThunk(
  'style/deactivate',
  async (styleId, { rejectWithValue }) => {
    try { return await styleService.deactivateStyle(styleId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchDocumentsThunk = createAsyncThunk(
  'style/fetchDocuments',
  async (_, { rejectWithValue }) => {
    try { return await styleService.listDocuments(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const uploadDocumentsThunk = createAsyncThunk(
  'style/uploadDocuments',
  async ({ files, docTag }, { rejectWithValue }) => {
    try {
      const formData = new FormData();
      files.forEach((f) => formData.append('files', f));
      if (docTag) formData.append('doc_tag', docTag);
      const result = await styleService.uploadDocuments(formData);
      toast.success(`${files.length} document(s) uploaded.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const regenerateStyleThunk = createAsyncThunk(
  'style/regenerate',
  async (styleId, { rejectWithValue }) => {
    try {
      const result = await styleService.regenerateStyle(styleId);
      toast.success('Style understanding regenerated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
