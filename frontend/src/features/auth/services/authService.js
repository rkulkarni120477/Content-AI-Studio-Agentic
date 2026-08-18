import { api } from '@services/apiClient';
import { AUTH } from '@services/endpoints';

// Empty default → relative, routed through Vite's /api proxy in dev. The
// Microsoft redirect below then becomes a same-origin nav that the proxy
// forwards. Prod sets VITE_API_BASE_URL to the real backend origin.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

export const authService = {
  // Password login: { username, password, organization_code?, platform_admin? }
  login:   (credentials) => api.post(AUTH.LOGIN, credentials),
  logout:  ()            => api.post(AUTH.LOGOUT),
  me:      ()            => api.get(AUTH.ME),
  refresh: ()            => api.post(AUTH.REFRESH),

  // Public sign-in options for the login screen.
  config:  ()            => api.get(AUTH.CONFIG),
  tenantLoginInfo: (slug) => api.get(AUTH.TENANT_LOGIN(slug)),

  // Microsoft OAuth is a full-page browser redirect, NOT an XHR.
  microsoftLoginUrl: (organizationCode) =>
    `${API_BASE_URL}${AUTH.MICROSOFT_LOGIN}?organization_code=${encodeURIComponent(organizationCode)}`,
};
