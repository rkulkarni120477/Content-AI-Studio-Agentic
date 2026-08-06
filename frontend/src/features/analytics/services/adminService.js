import { api } from '@services/apiClient';
import { ADMIN, DB_ADMIN, PLATFORM } from '@services/endpoints';

export const adminService = {
  getPermissionsOverview: () => api.get(ADMIN.PERMISSIONS_OVERVIEW),
  getPermissionMatrix:    () => api.get(ADMIN.PERMISSIONS),
  listClearPresets:       () => api.get(DB_ADMIN.CLEAR_PRESETS),
  clearPreset:            (tag) => api.post(DB_ADMIN.CLEAR_ENTITY(tag)),
  listBudgets:            (p) => api.get(PLATFORM.BUDGETS, { params: p }),
  upsertBudget:           (body) => api.put(PLATFORM.BUDGET_UPSERT, body),
  deleteBudget:           (id) => api.delete(PLATFORM.BUDGET_DELETE(id)),
};
