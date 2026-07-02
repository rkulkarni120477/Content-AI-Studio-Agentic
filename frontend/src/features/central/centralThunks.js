import { createAsyncThunk } from '@reduxjs/toolkit';
import { centralService } from './services/centralService';
import { promptsService } from '@features/prompts/services/promptsService';
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
  async (data, { rejectWithValue, dispatch }) => {
    try {
      await centralService.createItem(data);
      toast.success('Item added to Central Repository.');
      return dispatch(fetchCentralItemsThunk()).unwrap();
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const archiveCentralItemThunk = createAsyncThunk(
  'central/archive',
  async (itemId, { rejectWithValue, dispatch }) => {
    try {
      await centralService.archiveItem(itemId);
      toast.success('Item archived.');
      return dispatch(fetchCentralItemsThunk()).unwrap();
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** Import via Prompt Registry APIs + POST /admin/central (no dedicated import endpoint). */
export const importFromRegistryThunk = createAsyncThunk(
  'central/import',
  async (data, { rejectWithValue, dispatch }) => {
    try {
      const promptName = (data.prompt_name || '').trim();
      if (!promptName) return rejectWithValue('Prompt name is required.');

      const listed = await promptsService.listPrompts({ search: promptName, page_size: 100 });
      const match = listed.find((p) => p.name === promptName)
        || listed.find((p) => p.name?.toLowerCase() === promptName.toLowerCase());
      if (!match) return rejectWithValue(`No prompt asset named "${promptName}" found.`);

      const detail = await promptsService.getPromptDetail(match.id);
      let systemPrompt = detail.system_prompt || '';
      let userPrompt = detail.user_prompt_template || '';

      const versions = await promptsService.getVersions(match.id);
      const verRow = data.version
        ? versions.find(
          (v) => v.version_number === data.version || v.version === `v${data.version}`,
        )
        : versions.find((v) => v.is_active) || versions[versions.length - 1];
      if (verRow) {
        if (verRow.system_prompt) systemPrompt = verRow.system_prompt;
        if (verRow.user_prompt_template) userPrompt = verRow.user_prompt_template;
      }

      const content = [
        systemPrompt ? `## System Prompt\n\n${systemPrompt}` : '',
        userPrompt ? `## User Prompt Template\n\n${userPrompt}` : '',
      ].filter(Boolean).join('\n\n---\n\n');

      if (!content.trim()) {
        return rejectWithValue('Selected prompt has no text content to import.');
      }

      await centralService.createItem({
        name: `${promptName} (imported)`,
        item_type: 'Prompt',
        content,
        tags: `imported,prompt-registry,${detail.component_type || 'general'}`,
      });
      toast.success(`Imported "${promptName}" into Central Repository.`);
      return dispatch(fetchCentralItemsThunk()).unwrap();
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
