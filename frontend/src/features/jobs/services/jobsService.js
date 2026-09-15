import { api } from '@services/apiClient';
import { GENERATE } from '@services/endpoints';
import { POLL_REQUEST_CONFIG } from '@features/shared/blockJob';

export const jobsService = {
  listJobs: (courseId, opts = {}) =>
    api.get(GENERATE.JOB_LIST(courseId, opts), POLL_REQUEST_CONFIG),

  getJobStatus: (jobId) =>
    api.get(GENERATE.JOB_STATUS(jobId), POLL_REQUEST_CONFIG),

  cancelJob: (jobId) => api.delete(GENERATE.JOB_CANCEL(jobId)),
};
