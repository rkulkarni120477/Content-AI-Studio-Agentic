import { api } from '@services/apiClient';
import { PLATFORM } from '@services/endpoints';

export const platformService = {
  // Tenants (organizations)
  listTenants:  ()            => api.get(PLATFORM.TENANTS),
  createTenant: (body)        => api.post(PLATFORM.TENANTS, body),
  updateTenant: (id, body)    => api.put(PLATFORM.TENANT(id), body),
  deleteTenant: (id)          => api.delete(PLATFORM.TENANT(id)),
  tenantUsage:  (id)          => api.get(PLATFORM.TENANT_USAGE(id)),

  // Per-tenant members
  listMembers:  (id)          => api.get(PLATFORM.TENANT_USERS(id)),
  addMember:    (id, body)    => api.post(PLATFORM.TENANT_USERS(id), body),
  updateMember: (id, userId, body) => api.put(PLATFORM.TENANT_USER(id, userId), body),
  removeMember: (id, userId)  => api.delete(PLATFORM.TENANT_USER(id, userId)),

  // Per-tenant roles (system + custom)
  listRoles:    (id)          => api.get(PLATFORM.TENANT_ROLES(id)),
  createRole:   (id, body)    => api.post(PLATFORM.TENANT_ROLES(id), body),
  updateRole:   (id, roleId, body) => api.put(PLATFORM.TENANT_ROLE(id, roleId), body),
  deleteRole:   (id, roleId)  => api.delete(PLATFORM.TENANT_ROLE(id, roleId)),
  getPermissionCatalog: ()    => api.get(PLATFORM.PERMISSION_CATALOG),
};
