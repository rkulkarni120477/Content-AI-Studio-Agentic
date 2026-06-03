import { api } from '@services/apiClient';
import { CENTRAL } from '@services/endpoints';

export const centralService = {
  listItems:          (p)    => api.get(CENTRAL.LIST, { params: p }),
  createItem:         (data) => api.post(CENTRAL.CREATE, data),
  // Backend Central Repository routes live under /api/v1/admin/central
  // and use POST /central/{id}/archive for delete.
  deleteItem:         (id)   => api.post(CENTRAL.ARCHIVE(id)),
};
