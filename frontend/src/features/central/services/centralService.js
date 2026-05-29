import { api } from '@services/apiClient';
import { CENTRAL } from '@services/endpoints';

export const centralService = {
  listItems:          (p)    => api.get(CENTRAL.LIST, { params: p }),
  getItem:            (id)   => api.get(CENTRAL.GET(id)),
  createItem:         (data) => api.post(CENTRAL.CREATE, data),
  importFromRegistry: (data) => api.post(CENTRAL.IMPORT, data),
  deleteItem:         (id)   => api.delete(CENTRAL.DELETE(id)),
};
