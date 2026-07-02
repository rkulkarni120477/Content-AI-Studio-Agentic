import { createAsyncThunk } from '@reduxjs/toolkit';
import { authService } from './services/authService';
import { extractErrorMessage } from '@utils/helpers';

export const loginThunk = createAsyncThunk(
  'auth/login',
  async (credentials, { rejectWithValue }) => {
    try {
      return await authService.login(credentials);
    } catch (err) {
      return rejectWithValue(extractErrorMessage(err));
    }
  },
);

export const logoutThunk = createAsyncThunk(
  'auth/logout',
  async (_, { rejectWithValue }) => {
    try {
      await authService.logout();
    } catch {
      // Logout always succeeds client-side even if server call fails
    }
  },
);

export const fetchMeThunk = createAsyncThunk(
  'auth/fetchMe',
  async (_, { rejectWithValue }) => {
    try {
      return await authService.me();
    } catch (err) {
      return rejectWithValue(extractErrorMessage(err));
    }
  },
);
