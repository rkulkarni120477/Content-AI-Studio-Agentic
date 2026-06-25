import { createSlice } from '@reduxjs/toolkit';
import {
  fetchStylesThunk, createStyleThunk, activateStyleThunk,
  deactivateStyleThunk, fetchDocumentsThunk, uploadDocumentsThunk,
  regenerateStyleThunk,
  refineStyleThunk,
  deleteStyleThunk,
  updateStyleThunk,
  deleteDocumentThunk,
} from './styleThunks';
import { normalizeDocumentsPayload } from '@utils/documentRegistry';

const initialState = {
  styles:      [],
  documents:   [],
  documentStats: null,
  activeStyle: null,
  isLoading:   false,
  isUploadingDoc: false,
  generatingStyleId: null, // id of the style currently running Understand/Refine
  isCreating:     false,
  error:       null,
};

const styleSlice = createSlice({
  name: 'style',
  initialState,
  reducers: {
    clearError(s) { s.error = null; },
  },
  extraReducers: (b) => {
    b
      .addCase(fetchStylesThunk.pending,    (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchStylesThunk.fulfilled,  (s, { payload }) => {
        s.isLoading  = false;
        s.styles     = payload.items;
        s.activeStyle = payload.items.find((st) => st.is_active) || null;
      })
      .addCase(fetchStylesThunk.rejected,   (s, { payload }) => { s.isLoading = false; s.error = payload; })

      .addCase(createStyleThunk.pending,    (s) => { s.isCreating = true; s.error = null; })
      .addCase(createStyleThunk.fulfilled,  (s) => { s.isCreating = false; })
      .addCase(createStyleThunk.rejected,   (s, { payload }) => { s.isCreating = false; s.error = payload; })

      .addCase(activateStyleThunk.fulfilled, (s, { payload }) => {
        s.styles = s.styles.map((st) => ({ ...st, is_active: st.id === payload.id }));
        s.activeStyle = payload;
      })
      .addCase(deactivateStyleThunk.fulfilled, (s, { payload }) => {
        s.styles = s.styles.map((st) => st.id === payload.id ? payload : st);
        if (s.activeStyle?.id === payload.id) s.activeStyle = null;
      })

      .addCase(fetchDocumentsThunk.fulfilled, (s, { payload }) => {
        const { documents, stats } = normalizeDocumentsPayload(payload);
        s.documents = documents;
        if (stats) s.documentStats = stats;
      })

      .addCase(uploadDocumentsThunk.pending,  (s) => { s.isUploadingDoc = true; })
      .addCase(uploadDocumentsThunk.fulfilled,(s, { payload }) => {
        s.isUploadingDoc = false;
        const { documents, stats } = normalizeDocumentsPayload(payload);
        s.documents = documents;
        if (stats) s.documentStats = stats;
      })
      .addCase(uploadDocumentsThunk.rejected, (s, { payload }) => { s.isUploadingDoc = false; s.error = payload; })

      .addCase(deleteDocumentThunk.fulfilled, (s, { payload }) => {
        if (payload?.documents) {
          s.documents = payload.documents;
          if (payload.stats) s.documentStats = payload.stats;
        } else {
          s.documents = s.documents.filter((d) => d.id !== payload.id);
        }
      })

      .addCase(regenerateStyleThunk.pending,  (s, { meta }) => {
        s.generatingStyleId = meta.arg;
      })
      .addCase(regenerateStyleThunk.fulfilled,(s, { payload }) => {
        s.generatingStyleId = null;
        const styleId = payload?.style_id ?? payload?.id;
        const text = payload?.understanding ?? payload?.generated_summary ?? '';
        if (!styleId) return;
        s.styles = s.styles.map((st) => (
          st.id === styleId
            ? { ...st, understanding_preview: text ? String(text).slice(0, 200) : st.understanding_preview }
            : st
        ));
      })
      .addCase(regenerateStyleThunk.rejected, (s, { payload }) => {
        s.generatingStyleId = null;
        s.error = payload;
      })

      .addCase(refineStyleThunk.pending,   (s, { meta }) => {
        s.generatingStyleId = meta.arg?.styleId ?? null;
      })
      .addCase(refineStyleThunk.fulfilled, (s, { payload }) => {
        s.generatingStyleId = null;
        const styleId = payload?.style_id ?? payload?.id;
        const text = payload?.understanding ?? '';
        if (!styleId) return;
        s.styles = s.styles.map((st) => (
          st.id === styleId
            ? { ...st, understanding_preview: text ? String(text).slice(0, 200) : st.understanding_preview }
            : st
        ));
      })
      .addCase(refineStyleThunk.rejected,  (s, { payload }) => {
        s.generatingStyleId = null;
        s.error = payload;
      });

    b.addCase(updateStyleThunk.fulfilled, (s, { payload }) => {
      s.styles = s.styles.map((st) => st.id === payload.id ? payload : st);
      if (s.activeStyle?.id === payload.id) s.activeStyle = payload;
    });

    b.addCase(deleteStyleThunk.fulfilled, (s, { payload }) => {
      s.styles = s.styles.filter((st) => st.id !== payload.id);
      if (s.activeStyle?.id === payload.id) s.activeStyle = null;
    });
  },
});

export const { clearError } = styleSlice.actions;
export default styleSlice.reducer;

export const selectStyles      = (s) => s.style.styles;
export const selectDocuments   = (s) => s.style.documents;
export const selectDocumentStats = (s) => s.style.documentStats;
export const selectActiveStyle = (s) => s.style.activeStyle;
export const selectStyleLoading = (s) => s.style.isLoading;
export const selectGeneratingStyleId = (s) => s.style.generatingStyleId;
/** @deprecated use selectGeneratingStyleId for per-card loading */
export const selectStyleGenerating = (s) => s.style.generatingStyleId != null;
export const selectStyleCreating   = (s) => s.style.isCreating;
export const selectStyleError  = (s) => s.style.error;
