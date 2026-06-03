import { api } from '@services/apiClient';
import { ADMIN, DB_ADMIN } from '@services/endpoints';

export const adminService = {
  getPermissionsOverview: () => api.get(ADMIN.PERMISSIONS_OVERVIEW),
  getPermissionMatrix:    () => api.get(ADMIN.PERMISSIONS),
  listClearPresets:       () => api.get(DB_ADMIN.CLEAR_PRESETS),
  clearPreset:            (tag) => api.post(DB_ADMIN.CLEAR_ENTITY(tag)),
};
