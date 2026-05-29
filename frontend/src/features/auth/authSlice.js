import { createSlice } from '@reduxjs/toolkit';
import { tokenStorage } from '@utils/storage';
import { loginThunk, logoutThunk, fetchMeThunk } from './authThunks';

const initialState = {
  user:         null,   // { id, username, role, email, is_active, permissions }
  token:        tokenStorage.get() || null,
  isLoading:    false,
  isAuthChecked: false,
  error:        null,
};

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    clearError(state) {
      state.error = null;
    },
    forceLogout(state) {
      state.user    = null;
      state.token   = null;
      state.error   = null;
      tokenStorage.remove();
    },
  },
  extraReducers: (builder) => {
    // ── Login ────────────────────────────────────────────────────────────────
    builder.addCase(loginThunk.pending, (state) => {
      state.isLoading = true;
      state.error     = null;
    });
    builder.addCase(loginThunk.fulfilled, (state, { payload }) => {
      state.isLoading = false;
      state.token     = payload.access_token;
      state.user      = payload.user;
      tokenStorage.set(payload.access_token);
    });
    builder.addCase(loginThunk.rejected, (state, { payload }) => {
      state.isLoading = false;
      state.error     = payload;
    });

    // ── Logout ───────────────────────────────────────────────────────────────
    builder.addCase(logoutThunk.fulfilled, (state) => {
      state.user  = null;
      state.token = null;
      tokenStorage.remove();
    });

    // ── Fetch Me (boot-time auth check) ─────────────────────────────────────
    builder.addCase(fetchMeThunk.pending, (state) => {
      state.isLoading    = true;
    });
    builder.addCase(fetchMeThunk.fulfilled, (state, { payload }) => {
      state.isLoading     = false;
      state.isAuthChecked = true;
      state.user          = payload;
    });
    builder.addCase(fetchMeThunk.rejected, (state) => {
      state.isLoading     = false;
      state.isAuthChecked = true;
      state.user          = null;
      state.token         = null;
      tokenStorage.remove();
    });
  },
});

export const { clearError, forceLogout } = authSlice.actions;
export default authSlice.reducer;

// ─── Selectors ────────────────────────────────────────────────────────────────
export const selectUser          = (s) => s.auth.user;
export const selectToken         = (s) => s.auth.token;
export const selectIsAuthenticated = (s) => Boolean(s.auth.token && s.auth.user);
export const selectIsAuthChecked = (s) => s.auth.isAuthChecked;
export const selectAuthLoading   = (s) => s.auth.isLoading;
export const selectAuthError     = (s) => s.auth.error;
export const selectUserRole      = (s) => s.auth.user?.role;
export const selectIsAdmin       = (s) => s.auth.user?.role === 'admin';
export const selectIsReviewer    = (s) => ['admin', 'reviewer'].includes(s.auth.user?.role);
