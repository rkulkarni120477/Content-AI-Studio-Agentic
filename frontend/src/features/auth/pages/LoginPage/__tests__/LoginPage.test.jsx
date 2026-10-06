// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { MemoryRouter } from 'react-router-dom';

const { login } = vi.hoisted(() => ({ login: vi.fn() }));

vi.mock('@features/auth/services/authService', () => ({
  authService: {
    login,
    logout: vi.fn(),
    me: vi.fn(),
    refresh: vi.fn(),
    config: vi.fn().mockResolvedValue({ microsoft_enabled: false, local_login_enabled: true }),
    tenantLoginInfo: vi.fn(),
    microsoftLoginUrl: vi.fn(),
  },
}));

import authReducer from '@features/auth/authSlice';
import LoginPage from '../LoginPage';

afterEach(() => {
  cleanup();
  login.mockReset();
});

it('requires an organization code unless platform administrator mode is selected', () => {
  const store = configureStore({
    reducer: { auth: authReducer },
    preloadedState: {
      auth: {
        user: null,
        token: null,
        isLoading: false,
        isAuthChecked: true,
        error: null,
        project_id: null,
        is_platform_admin: false,
      },
    },
  });

  render(
    <Provider store={store}>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </Provider>,
  );

  fireEvent.change(screen.getByPlaceholderText('Enter username'), {
    target: { value: 'admin' },
  });
  fireEvent.change(screen.getByPlaceholderText('Enter password'), {
    target: { value: 'password' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));

  expect(screen.getByRole('alert').textContent).toContain('select Platform administrator');
  expect(login).not.toHaveBeenCalled();
});
