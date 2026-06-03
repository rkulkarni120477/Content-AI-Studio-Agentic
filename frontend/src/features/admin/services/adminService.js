import { api } from '@services/apiClient';
import { ADMIN } from '@services/endpoints';

export const adminService = {
  listInstructions: (params) => api.get(ADMIN.INSTRUCTIONS, { params }),
  saveInstruction:  (data)  => api.post(ADMIN.INSTRUCTIONS, data),
};
