import { api } from '@services/apiClient';
import { DOCUMENTS } from '@services/endpoints';

export const documentService = {
  listActive: async () => {
    const res = await api.get(DOCUMENTS.LIST, { params: { page: 1, page_size: 500 } });
    return res.items || [];
  },

  parseFile: async (file, sourceType = 'reference') => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('source_type', sourceType);
    return api.upload(DOCUMENTS.PARSE, formData);
  },
};
