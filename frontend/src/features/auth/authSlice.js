import { createSlice } from '@reduxjs/toolkit';
import { tokenStorage } from '@utils/storage';
import { loginThunk, logoutThunk, fetchMeThunk } from './authThunks';

const initialState = {
  user:         null,   // { id, username, role, email, is_active, permissions }
  token:        tokenStorage.get() || null,
  isLoading:    false,
  isAuthChecked: false,
  error:        null,
  project_id:        null,
  is_platform_admin: false,
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
      state.project_id        = null;
      state.is_platform_admin = false;
      tokenStorage.remove();
    },
    setToken(state, { payload }) {
      state.token = payload;
      tokenStorage.set(payload);
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
      state.project_id        = payload.user.project_id        ?? null;
      state.is_platform_admin = payload.user.is_platform_admin ?? false;
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
      state.project_id        = null;
      state.is_platform_admin = false;
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
      state.project_id        = payload.project_id        ?? null;
      state.is_platform_admin = payload.is_platform_admin ?? false;
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

export const { clearError, forceLogout, setToken } = authSlice.actions;
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
export const selectProjectId       = (s) => s.auth.project_id;
export const selectIsPlatformAdmin = (s) => s.auth.is_platform_admin;
