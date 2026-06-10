import { api } from '@services/apiClient';
import { CENTRAL } from '@services/endpoints';

function normalizeItem(row) {
  if (!row) return row;
  return {
    ...row,
    name: row.title ?? row.name ?? '',
    item_type: row.item_type ?? (row.tags?.includes('Prompt') ? 'Prompt' : 'Asset'),
  };
}

export const centralService = {
  listItems: async (params = {}) => {
    const res = await api.get(CENTRAL.LIST, { params });
    const items = (res.items || []).map(normalizeItem);
    return {
      items,
      total: res.total ?? items.length,
      page: res.page ?? 1,
      page_size: res.page_size ?? items.length,
    };
  },

  createItem: (data) => api.post(CENTRAL.CREATE, {
    title: (data.name || data.title || '').trim(),
    item_type: data.item_type || 'Prompt',
    content: data.content || '',
    tags: data.tags || '',
    project_id: data.project_id ?? null,
    course_id: data.course_id ?? null,
  }),

  archiveItem: (id) => api.post(CENTRAL.ARCHIVE(id)),
};
