import { createAsyncThunk } from '@reduxjs/toolkit';
import { editorService } from './services/editorService';
import { extractErrorMessage } from '@utils/helpers';
import { downloadBlob } from '@utils/helpers';
import toast from 'react-hot-toast';

export const fetchBlocksThunk = createAsyncThunk(
  'editor/fetchBlocks',
  async (courseId, { rejectWithValue }) => {
    try { return await editorService.listBlocks(courseId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const updateBlockThunk = createAsyncThunk(
  'editor/updateBlock',
  async ({ blockId, data }, { rejectWithValue }) => {
    try {
      const result = await editorService.updateBlock(blockId, data);
      toast.success('Block saved.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchBlockVersionsThunk = createAsyncThunk(
  'editor/fetchBlockVersions',
  async (blockId, { rejectWithValue }) => {
    try { return await editorService.getBlockVersions(blockId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const restoreBlockVersionThunk = createAsyncThunk(
  'editor/restoreVersion',
  async ({ blockId, version }, { rejectWithValue }) => {
    try {
      const result = await editorService.restoreVersion(blockId, version);
      toast.success('Version restored.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const submitBlockThunk = createAsyncThunk(
  'editor/submitBlock',
  async ({ blockId, action, data }, { rejectWithValue }) => {
    try {
      const result = await editorService.submitWorkflowAction(blockId, action, data);
      toast.success(`Block ${action.replace('_', ' ')} successfully.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const exportBlockThunk = createAsyncThunk(
  'editor/exportBlock',
  async ({ blockId, format, filename }, { rejectWithValue }) => {
    try {
      const response = await editorService.exportBlock(blockId, format);
      downloadBlob(response.data, filename || `block-${blockId}.${format}`);
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const exportCourseThunk = createAsyncThunk(
  'editor/exportCourse',
  async ({ courseId, format, filename }, { rejectWithValue }) => {
    try {
      const response = await editorService.exportCourse(courseId, format);
      downloadBlob(response.data, filename || `course-${courseId}.${format}`);
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
      await editorService.triggerPlagiarism(blockId);
      toast.success('Plagiarism scan queued.');
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const validateCourseThunk = createAsyncThunk(
  'editor/validate',
  async (courseId, { rejectWithValue }) => {
    try { return await editorService.validateCourse(courseId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
