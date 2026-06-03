import { createSlice } from '@reduxjs/toolkit';
import {
  fetchStylesThunk, createStyleThunk, activateStyleThunk,
  deactivateStyleThunk, fetchDocumentsThunk, uploadDocumentsThunk,
  regenerateStyleThunk,
  deleteStyleThunk,
  updateStyleThunk,
  deleteDocumentThunk,
} from './styleThunks';

const initialState = {
  styles:      [],
  documents:   [],
  activeStyle: null,
  isLoading:   false,
  isUploadingDoc: false,
  isGenerating:   false,
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

      .addCase(createStyleThunk.pending,    (s) => { s.isGenerating = true; })
      .addCase(createStyleThunk.fulfilled,  (s, { payload }) => {
        s.isGenerating = false;
        s.styles.unshift(payload);
        if (payload.is_active) s.activeStyle = payload;
      })
      .addCase(createStyleThunk.rejected,   (s, { payload }) => { s.isGenerating = false; s.error = payload; })

      .addCase(activateStyleThunk.fulfilled, (s, { payload }) => {
        s.styles = s.styles.map((st) => ({ ...st, is_active: st.id === payload.id }));
        s.activeStyle = payload;
      })
      .addCase(deactivateStyleThunk.fulfilled, (s, { payload }) => {
        s.styles = s.styles.map((st) => st.id === payload.id ? payload : st);
        if (s.activeStyle?.id === payload.id) s.activeStyle = null;
      })

      .addCase(fetchDocumentsThunk.fulfilled, (s, { payload }) => { s.documents = payload; })

      .addCase(uploadDocumentsThunk.pending,  (s) => { s.isUploadingDoc = true; })
      .addCase(uploadDocumentsThunk.fulfilled,(s, { payload }) => {
        s.isUploadingDoc = false;
        s.documents = Array.isArray(payload) ? payload : [];
      })
      .addCase(uploadDocumentsThunk.rejected, (s, { payload }) => { s.isUploadingDoc = false; s.error = payload; })

      .addCase(deleteDocumentThunk.fulfilled, (s, { payload }) => {
        s.documents = s.documents.filter((d) => d.id !== payload.id);
      })

      .addCase(regenerateStyleThunk.pending,  (s) => { s.isGenerating = true; })
      .addCase(regenerateStyleThunk.fulfilled,(s, { payload }) => {
        s.isGenerating = false;
        s.styles = s.styles.map((st) => st.id === payload.id ? payload : st);
        if (s.activeStyle?.id === payload.id) s.activeStyle = payload;
      })
      .addCase(regenerateStyleThunk.rejected, (s, { payload }) => { s.isGenerating = false; s.error = payload; });

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
export const selectActiveStyle = (s) => s.style.activeStyle;
export const selectStyleLoading = (s) => s.style.isLoading;
export const selectStyleGenerating = (s) => s.style.isGenerating;
export const selectStyleError  = (s) => s.style.error;
