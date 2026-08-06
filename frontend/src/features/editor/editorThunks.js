import { createAsyncThunk } from '@reduxjs/toolkit';
import { editorService } from './services/editorService';
import { extractErrorMessage, formatUsageSummaryMessage, hasOverBudget } from '@utils/helpers';
import { downloadBlob } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchGenerationsThunk = createAsyncThunk(
  'editor/fetchGenerations',
  async ({ courseId, projectId, blueprintId, cddId }, { rejectWithValue }) => {
    try {
      return await editorService.listGenerations({
        courseId,
        projectId,
        blueprintId,
        cddId,
        pageSize: 500,
      });
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const fetchGenerationBlocksThunk = createAsyncThunk(
  'editor/fetchGenerationBlocks',
  async (generationId, { rejectWithValue }) => {
    try {
      let items = [];
      try {
        items = await editorService.listBlocksForGeneration(generationId);
      } catch {
        /* list endpoint may fail — fall back to generation detail */
      }
      if (!items.length) {
        const gen = await editorService.getGeneration(generationId);
        items = (gen?.blocks || []).map((b) => ({
          id: b.id,
          block_label: b.block_label,
          workflow_state: b.workflow_state,
          content_preview: '',
        }));
      }
      if (!items.length) return [];

      const full = await Promise.all(
        items.map(async (item) => {
          try {
            return await editorService.getBlock(item.id);
          } catch {
            return {
              ...item,
              content: item.content || item.content_preview || '',
              block_label: item.block_label || `Block ${item.id}`,
              workflow_state: item.workflow_state || 'draft',
            };
          }
        }),
      );
      return full.filter(Boolean);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const fetchCourseBlocksThunk = createAsyncThunk(
  'editor/fetchCourseBlocks',
  async (courseId, { rejectWithValue }) => {
    try {
      return await editorService.listCourseBlocks(courseId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

/** @deprecated use fetchGenerationBlocksThunk */
export const fetchBlocksThunk = fetchCourseBlocksThunk;

export const updateBlockThunk = createAsyncThunk(
  'editor/updateBlock',
  async ({ blockId, data }, { rejectWithValue }) => {
    try {
      const result = await editorService.updateBlock(blockId, data);
      toast.success('Block saved.');
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const autosaveBlockThunk = createAsyncThunk(
  'editor/autosaveBlock',
  async ({ blockId, content }, { rejectWithValue }) => {
    try {
      return await editorService.autosaveBlock(blockId, content);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const regenerateBlockThunk = createAsyncThunk(
  'editor/regenerateBlock',
  async ({ blockId, instruction, modelChoice }, { rejectWithValue }) => {
    try {
      const result = await editorService.regenerateBlock(blockId, {
        feedback_instruction: instruction,
        model_choice: modelChoice,
      });
      const usageMsg = formatUsageSummaryMessage(result.usage_summary);
      const message = usageMsg ? `Block regenerated. ${usageMsg}` : 'Block regenerated.';
      if (hasOverBudget(result.usage_summary)) toast.error(message); else toast.success(message);
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const fetchBlockVersionsThunk = createAsyncThunk(
  'editor/fetchBlockVersions',
  async (blockId, { rejectWithValue }) => {
    try {
      return await editorService.getBlockVersions(blockId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const restoreBlockVersionThunk = createAsyncThunk(
  'editor/restoreVersion',
  async ({ blockId, versionId }, { rejectWithValue }) => {
    try {
      const result = await editorService.restoreVersion(blockId, versionId);
      toast.success('Version restored.');
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const createSnapshotThunk = createAsyncThunk(
  'editor/createSnapshot',
  async ({ blockId, label }, { rejectWithValue }) => {
    try {
      await editorService.createSnapshot(blockId, label);
      toast.success('Snapshot saved.');
      return blockId;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const submitBlockThunk = createAsyncThunk(
  'editor/submitBlock',
  async ({ blockId, action, data }, { rejectWithValue }) => {
    try {
      const result = await editorService.submitWorkflowAction(blockId, action, data);
      toast.success(`Block ${action.replace(/_/g, ' ')} successfully.`);
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const exportGenerationThunk = createAsyncThunk(
  'editor/exportGeneration',
  async ({ generationId, format, template, filename }, { rejectWithValue }) => {
    try {
      const response = await editorService.exportGeneration(generationId, format, template);
      downloadBlob(response.data, filename);
      toast.success('Export downloaded.');
    } catch (e) {
      toast.error('Export failed.');
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const exportCourseThunk = createAsyncThunk(
  'editor/exportCourse',
  async ({ courseId, format, template, filename }, { rejectWithValue }) => {
    try {
      const response = await editorService.exportCourse(courseId, format, template);
      downloadBlob(response.data, filename);
      toast.success('Export downloaded.');
    } catch (e) {
      toast.error('Export failed.');
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const triggerPlagiarismThunk = createAsyncThunk(
  'editor/plagiarism',
  async (blockId, { rejectWithValue }) => {
    try {
      return await editorService.triggerPlagiarism(blockId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const fetchPlagiarismStatusThunk = createAsyncThunk(
  'editor/plagiarismStatus',
  async ({ blockId, reportId }, { rejectWithValue }) => {
    try {
      return await editorService.getPlagiarismStatus(blockId, reportId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const validateGenerationThunk = createAsyncThunk(
  'editor/validateGeneration',
  async (generationId, { rejectWithValue }) => {
    try {
      return await editorService.validateGeneration(generationId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const validateCourseThunk = createAsyncThunk(
  'editor/validateCourse',
  async (courseId, { rejectWithValue }) => {
    try {
      return await editorService.validateCourse(courseId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const regenerateBlockItemThunk = createAsyncThunk(
  'editor/regenerateBlockItem',
  async ({ blockId, itemIndex, sectionKey, feedback, modelChoice }, { rejectWithValue }) => {
    try {
      const result = await editorService.regenerateBlockItem(blockId, {
        item_index: itemIndex,
        section_key: sectionKey,
        feedback: feedback || '',
        model_choice: modelChoice,
      });
      const usageMsg = formatUsageSummaryMessage(result.usage_summary);
      const message = usageMsg ? `Item regenerated. ${usageMsg}` : 'Item regenerated.';
      if (hasOverBudget(result.usage_summary)) toast.error(message); else toast.success(message);
      return result;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const scoreBlockThunk = createAsyncThunk(
  'editor/scoreBlock',
  async (blockId, { rejectWithValue }) => {
    try {
      return await editorService.scoreBlock(blockId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

