import { createAsyncThunk } from '@reduxjs/toolkit';
import { cddService } from './services/cddService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchCddsThunk = createAsyncThunk(
  'cdd/fetch',
  async (courseId, { rejectWithValue }) => {
    try { return await cddService.listCdds(courseId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const generateCddThunk = createAsyncThunk(
  'cdd/generate',
  async (payload, { rejectWithValue }) => {
    try {
      const result = await cddService.generateCdd(payload);
      toast.success('CDD generated and set as active.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const setActiveCddThunk = createAsyncThunk(
  'cdd/setActive',
  async ({ cddId, courseId }, { rejectWithValue }) => {
    try {
      const result = await cddService.setActiveCdd(cddId, courseId);
      toast.success('Active CDD updated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchCddVersionsThunk = createAsyncThunk(
  'cdd/fetchVersions',
  async (cddId, { rejectWithValue }) => {
    try { return await cddService.getVersions(cddId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const commitCddVersionThunk = createAsyncThunk(
  'cdd/commitVersion',
  async ({ cddId, data }, { rejectWithValue }) => {
    try {
      const result = await cddService.commitVersion(cddId, data);
      toast.success('New version saved.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const exportCddThunk = createAsyncThunk(
  'cdd/export',
  async ({ cddId, format }, { rejectWithValue }) => {
    try { return await cddService.exportCdd(cddId, format); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
