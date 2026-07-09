import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { RouterProvider } from 'react-router-dom';
import store from '@app/store';
import { router } from '@app/routes';
import { fetchMeThunk } from '@features/auth/authThunks';
import { forceLogout } from '@features/auth/authSlice';
import { tokenStorage } from '@utils/storage';
import '@styles/global.scss';

// Boot-time auth check — only validate against the backend when a token exists.
// With no token there is nothing to validate: a bare GET /me would 401, flip the
// auth loading state, and flash the login screen before any input. forceLogout
// marks the auth check as settled so guards don't hang on the boot loader.
if (tokenStorage.get()) {
  store.dispatch(fetchMeThunk());
} else {
  store.dispatch(forceLogout());
}

// Listen for 401 events dispatched by the Axios interceptor
window.addEventListener('auth:logout', () => {
  store.dispatch(forceLogout());
});

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Provider store={store}>
      <RouterProvider router={router} />
    </Provider>
  </StrictMode>,
);
