import { createAsyncThunk } from '@reduxjs/toolkit';
import { promptsService } from './services/promptsService';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchPromptsThunk = createAsyncThunk(
  'prompts/fetch',
  async (params = {}, { rejectWithValue }) => {
    try { return await promptsService.listPrompts(params); }
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
  async (promptId, { rejectWithValue }) => {
    try { return await promptsService.getVersions(promptId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const aiGeneratePromptThunk = createAsyncThunk(
  'prompts/aiGenerate',
  async (description, { rejectWithValue }) => {
    try {
      return await promptsService.aiSuggest({ description });
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
