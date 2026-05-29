import { createAsyncThunk } from '@reduxjs/toolkit';
import { blueprintService } from './services/blueprintService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchBlueprintsThunk = createAsyncThunk(
  'blueprint/fetch',
  async (courseId, { rejectWithValue }) => {
    try { return await blueprintService.listBlueprints(courseId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const generateBlueprintThunk = createAsyncThunk(
  'blueprint/generate',
  async (payload, { rejectWithValue }) => {
    try {
      const result = await blueprintService.generateBlueprint(payload);
      toast.success('Blueprint generated and set as active.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const setActiveBlueprintThunk = createAsyncThunk(
  'blueprint/setActive',
  async ({ blueprintId, courseId }, { rejectWithValue }) => {
    try {
      const result = await blueprintService.setActiveBlueprint(blueprintId, courseId);
      toast.success('Active blueprint updated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchBlueprintVersionsThunk = createAsyncThunk(
  'blueprint/fetchVersions',
  async (blueprintId, { rejectWithValue }) => {
    try { return await blueprintService.getVersions(blueprintId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const commitBlueprintVersionThunk = createAsyncThunk(
  'blueprint/commitVersion',
  async ({ blueprintId, data }, { rejectWithValue }) => {
    try {
      const result = await blueprintService.commitVersion(blueprintId, data);
      toast.success('Blueprint version saved.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchBlueprintComponentsThunk = createAsyncThunk(
  'blueprint/fetchComponents',
  async (blueprintId, { rejectWithValue }) => {
    try { return await blueprintService.getComponents(blueprintId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
