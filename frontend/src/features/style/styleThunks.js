import { createAsyncThunk } from '@reduxjs/toolkit';
import { styleService } from './services/styleService';
import { analyticsService } from '@features/analytics/services/analyticsService';
import { computeDocumentRegistryStats } from '@utils/documentRegistry';
import { extractErrorMessage } from '@utils/helpers';
import { labelsFromState } from '@config/tenantLabels';
import toast from 'react-hot-toast';

async function loadDocumentRegistry() {
  const [documents, summary, uploadHistory] = await Promise.all([
    styleService.listAllDocuments(),
    analyticsService.getSummary().catch(() => null),
    analyticsService.getDocumentUploadHistory({ limit: 100 }).catch(() => []),
  ]);
  return {
    documents,
    stats: computeDocumentRegistryStats(documents, summary, uploadHistory),
  };
}

export const fetchStylesThunk = createAsyncThunk(
  'style/fetchStyles',
  async (_, { getState, rejectWithValue }) => {
    try {
      const state = getState();
      const courseId = state?.dashboard?.selectedCourse?.id ?? null;
      const projectId = state?.dashboard?.selectedProject?.id ?? null;
      return await styleService.listStyles({ projectId, courseId });
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const createStyleThunk = createAsyncThunk(
  'style/create',
  async (
    { name, description, custom_instructions, document_ids = [], newFiles = [] },
    { getState, rejectWithValue, dispatch },
  ) => {
    try {
      const state = getState();
      const courseId = state?.dashboard?.selectedCourse?.id;
      const projectId = state?.dashboard?.selectedProject?.id;
      const allDocIds = [...document_ids];

      for (const file of newFiles) {
        const doc = await styleService.uploadLibraryFile(file, 'style_reference');
        if (doc?.id) allDocIds.push(doc.id);
      }

      const result = await styleService.createStyle({
        name: name.trim(),
        description: description?.trim() || null,
        custom_instructions: custom_instructions?.trim() || null,
        document_ids: [...new Set(allDocIds)],
        activate: true,
        course_id: courseId ?? null,
        project_id: projectId ?? null,
      });

      const L = labelsFromState(getState);
      toast.success(`${L.style} "${result.name}" created and activated for this ${L.titleLower}.`);
      await dispatch(fetchStylesThunk());
      await dispatch(fetchDocumentsThunk());
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const activateStyleThunk = createAsyncThunk(
  'style/activate',
  async ({ styleId, scope = 'course' }, { getState, rejectWithValue }) => {
    try {
      const state = getState();
      const courseId = state?.dashboard?.selectedCourse?.id ?? null;
      const projectId = state?.dashboard?.selectedProject?.id ?? null;
      let body = {};
      if (scope === 'course') {
        body = { course_id: courseId, project_id: projectId };
      } else if (scope === 'project') {
        body = { project_id: projectId };
      }
      // scope === 'global' → empty body activates globally (admin only in UI)
      const result = await styleService.activateStyle(styleId, body);
      const L = labelsFromState(getState);
      const scopeLabel = scope === 'global'
        ? 'globally'
        : scope === 'project'
          ? 'for this project'
          : `for this ${L.titleLower}`;
      toast.success(`${L.style} activated ${scopeLabel}.`);
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
    try { return await loadDocumentRegistry(); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const uploadDocumentsThunk = createAsyncThunk(
  'style/uploadDocuments',
  async ({ files, sourceType }, { rejectWithValue }) => {
    try {
      const uploaded = [];
      for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('source_type', sourceType || 'reference');
        const doc = await styleService.uploadDocuments(formData);
        uploaded.push(doc);
      }

      toast.success(`${uploaded.length} document(s) added.`);
      return await loadDocumentRegistry();
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const regenerateStyleThunk = createAsyncThunk(
  'style/regenerate',
  async (arg, { getState, rejectWithValue }) => {
    // Accept either a bare styleId (legacy) or { styleId, promptId }.
    const styleId = (arg && typeof arg === 'object') ? arg.styleId : arg;
    const promptId = (arg && typeof arg === 'object') ? arg.promptId : undefined;
    try {
      const state = getState();
      const modelChoice = state?.dashboard?.modelChoice || 'GPT-5.4';
      const result = await styleService.regenerateStyle(styleId, {
        model_choice: modelChoice,
        extra_instructions: '',
        course_id: state?.dashboard?.selectedCourse?.id ?? null,
        project_id: state?.dashboard?.selectedProject?.id ?? null,
        prompt_id: promptId ?? undefined,
      });
      toast.success(`${labelsFromState(getState).style} understanding generated.`);
      return result;
    } catch (e) {
      toast.error(extractErrorMessage(e));
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const refineStyleThunk = createAsyncThunk(
  'style/refine',
  async ({ styleId, corrections }, { getState, rejectWithValue }) => {
    try {
      const state = getState();
      const modelChoice = state?.dashboard?.modelChoice || 'GPT-5.4';
      const result = await styleService.regenerateStyle(styleId, {
        model_choice: modelChoice,
        extra_instructions: corrections,
        course_id: state?.dashboard?.selectedCourse?.id ?? null,
        project_id: state?.dashboard?.selectedProject?.id ?? null,
      });
      toast.success(`Refined ${labelsFromState(getState).style} Intelligence Layer saved.`);
      return result;
    } catch (e) {
      toast.error(extractErrorMessage(e));
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const deleteStyleThunk = createAsyncThunk(
  'style/delete',
  async (styleId, { getState, rejectWithValue }) => {
    try {
      await styleService.deleteStyle(styleId);
      toast.success(`${labelsFromState(getState).style} deleted.`);
      return { id: styleId };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const updateStyleThunk = createAsyncThunk(
  'style/update',
  async ({ styleId, data }, { getState, rejectWithValue }) => {
    try {
      const result = await styleService.updateStyle(styleId, data);
      toast.success(`${labelsFromState(getState).style} updated.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const uploadStyleDocsThunk = createAsyncThunk(
  'style/uploadStyleDocs',
  async ({ styleId, files = [], documentIds = [], additionalInstructions = '' }, { rejectWithValue }) => {
    try {
      const formData = new FormData();
      if (documentIds.length) {
        formData.append('document_ids', JSON.stringify(documentIds));
      }
      if (additionalInstructions?.trim()) {
        formData.append('additional_instructions', additionalInstructions.trim());
      }
      files.forEach((f) => formData.append('files', f));
      const result = await styleService.uploadStyleDocs(styleId, formData);
      if (result?.errors?.length) {
        toast.error(`Some files failed: ${result.errors[0]}`);
      }
      const added = result?.added ?? files.length + documentIds.length;
      if (added > 0) {
        toast.success(`${added} file(s) added. Understanding marked as stale.`);
      }
      return { styleId, result };
    } catch (e) {
      toast.error(extractErrorMessage(e));
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const deleteDocumentThunk = createAsyncThunk(
  'style/deleteDocument',
  async (documentId, { rejectWithValue }) => {
    try {
      await styleService.deleteDocument(documentId);
      toast.success('Document archived.');
      return { id: documentId, ...(await loadDocumentRegistry()) };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
