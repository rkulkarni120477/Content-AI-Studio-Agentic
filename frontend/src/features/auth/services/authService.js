import { api } from '@services/apiClient';
import { AUTH } from '@services/endpoints';

export const authService = {
  login: (credentials) => api.post(AUTH.LOGIN, credentials),
  logout: ()           => api.post(AUTH.LOGOUT),
  me: ()               => api.get(AUTH.ME),
  refresh: ()          => api.post(AUTH.REFRESH),
};
