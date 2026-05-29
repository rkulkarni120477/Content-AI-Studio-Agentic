import { createSlice } from '@reduxjs/toolkit';
import { fetchCentralItemsThunk, createCentralItemThunk, importFromRegistryThunk } from './centralThunks';

const initialState = {
  items:     [],
  total:     0,
  isLoading: false,
  error:     null,
};

const centralSlice = createSlice({
  name: 'central',
  initialState,
  reducers: { clearError(s) { s.error = null; } },
  extraReducers: (b) => {
    b
      .addCase(fetchCentralItemsThunk.pending,   (s) => { s.isLoading = true; s.error = null; })
      .addCase(fetchCentralItemsThunk.fulfilled, (s, { payload }) => {
        s.isLoading = false;
        s.items     = payload.items;
        s.total     = payload.total;
      })
      .addCase(fetchCentralItemsThunk.rejected,  (s, { payload }) => { s.isLoading = false; s.error = payload; })
      .addCase(createCentralItemThunk.fulfilled, (s, { payload }) => { s.items.unshift(payload); s.total += 1; })
      .addCase(importFromRegistryThunk.fulfilled,(s, { payload }) => { s.items.unshift(payload); s.total += 1; });
  },
});

export const { clearError } = centralSlice.actions;
export default centralSlice.reducer;

export const selectCentralItems   = (s) => s.central.items;
export const selectCentralTotal   = (s) => s.central.total;
export const selectCentralLoading = (s) => s.central.isLoading;
export const selectCentralError   = (s) => s.central.error;
