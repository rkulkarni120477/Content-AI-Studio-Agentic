import { api } from '@services/apiClient';
import { PLATFORM } from '@services/endpoints';

export const platformService = {
  listTenants:      ()                          => api.get(PLATFORM.TENANTS),
  createTenant:     (body)                      => api.post(PLATFORM.TENANTS, body),
  updateTenant:     (tenantId, body)            => api.put(PLATFORM.TENANT(tenantId), body),
  listTenantUsers:  (tenantId)                  => api.get(PLATFORM.USERS(tenantId)),
  createTenantUser: (tenantId, body)            => api.post(PLATFORM.USERS(tenantId), body),
  updateTenantUser: (tenantId, username, body)  => api.put(PLATFORM.USER(tenantId, username), body),
  deleteTenantUser: (tenantId, username)        => api.delete(PLATFORM.USER(tenantId, username)),
  getTenantUsage:   (tenantId)                  => api.get(PLATFORM.USAGE(tenantId)),
};
