import { api } from '@services/apiClient';
import { GENERATE } from '@services/endpoints';

export const generateService = {
  run:          (data)  => api.post(GENERATE.RUN, data),
  queue:        (data)  => api.post(GENERATE.QUEUE, data),
  getJobStatus: (jobId) => api.get(GENERATE.STATUS(jobId)),
  cancelJob:    (jobId) => api.post(GENERATE.CANCEL(jobId)),
};
