import { createAsyncThunk } from '@reduxjs/toolkit';
import { promptsService } from './services/promptsService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchPromptsThunk = createAsyncThunk(
  'prompts/fetch',
  async (_, { rejectWithValue }) => {
    try { return await promptsService.listPrompts(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const commitPromptThunk = createAsyncThunk(
  'prompts/commit',
  async (data, { rejectWithValue }) => {
    try {
      const result = await promptsService.commitPrompt(data);
      toast.success('Prompt asset saved.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchPromptVersionsThunk = createAsyncThunk(
  'prompts/fetchVersions',
  async (name, { rejectWithValue }) => {
    try { return await promptsService.getVersions(name); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const aiGeneratePromptThunk = createAsyncThunk(
  'prompts/aiGenerate',
  async (description, { rejectWithValue }) => {
    try { return await promptsService.aiGenerate({ description }); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
