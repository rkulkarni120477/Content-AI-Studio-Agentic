import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { RouterProvider } from 'react-router-dom';
import store from '@app/store';
import { router } from '@app/routes';
import { fetchMeThunk } from '@features/auth/authThunks';
import { forceLogout } from '@features/auth/authSlice';
import '@styles/global.scss';

// Boot-time auth check — validates the stored JWT against the backend
store.dispatch(fetchMeThunk());

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
