import { api } from '@services/apiClient';
import { PLATFORM } from '@services/endpoints';

export const platformService = {
  // Tenants (organizations)
  listTenants:  ()            => api.get(PLATFORM.TENANTS),
  createTenant: (body)        => api.post(PLATFORM.TENANTS, body),
  updateTenant: (id, body)    => api.put(PLATFORM.TENANT(id), body),
  tenantUsage:  (id)          => api.get(PLATFORM.TENANT_USAGE(id)),

  // Per-tenant members
  listMembers:  (id)          => api.get(PLATFORM.TENANT_USERS(id)),
  addMember:    (id, body)    => api.post(PLATFORM.TENANT_USERS(id), body),
  updateMember: (id, userId, body) => api.put(PLATFORM.TENANT_USER(id, userId), body),
  removeMember: (id, userId)  => api.delete(PLATFORM.TENANT_USER(id, userId)),
};
