import { api } from '@services/apiClient';
import { CDD, COURSES, GENERATE } from '@services/endpoints';
// Poll requests carry their own short timeout instead of the app-wide 120s one —
// see blockJob.POLL_REQUEST_TIMEOUT_MS for why that default was the wrong tool here.
import { POLL_REQUEST_CONFIG } from '@features/shared/blockJob';

/**
 * Payload for block-wide (digest-pipeline) async generation. Deliberately
 * narrower than the sync path — the digest pipeline builds its own context from
 * DIS enumerate/digests, so reference-doc/style/prompt-override fields don't apply.
 */
function mapBlockPayload(data) {
  return {
    deliverable: 'cdd',
    block: data.block,
    course_id: data.course_id,
    project_id: data.project_id,
    course_title: data.course_title || '',
    document_title: data.document_title || undefined,
    quality_tier: data.quality_tier || undefined,
    model_choice: data.model_choice || undefined,
    extra_instructions: data.extra_instructions || '',
    target_audience: data.target_audience || '',
    expert_domain: data.expert_domain || '',
    estimated_duration_hours: data.estimated_duration_hours ?? undefined,
  };
}

function mapGeneratePayload(data) {
  return {
    course_id: data.course_id,
    project_id: data.project_id,
    course_title: data.course_title,
    document_title: data.document_title || undefined,
    estimated_duration_hours: data.estimated_duration_hours ?? data.duration_hours ?? 8,
    extra_instructions: data.extra_instructions || '',
    style_id: data.style_id ?? null,
    reference_document_ids: data.reference_document_ids || [],
    model_choice: data.model_choice,
    target_audience: data.target_audience || '',
    expert_domain: data.expert_domain || '',
    audience_category: data.audience_category || 'Professional/Corporate',
    system_prompt_override: data.system_prompt_override || undefined,
    user_prompt_override: data.user_prompt_override || undefined,
    prompt_id: data.prompt_id ?? undefined,
  };
}

export const cddService = {
  listCdds: async (courseId, params = {}) => {
    const query = {
      page: 1,
      page_size: 100,
      ...params,
    };
    if (courseId) query.course_id = courseId;
    const res = await api.get(CDD.LIST(), { params: query });
    return res?.items ?? (Array.isArray(res) ? res : []);
  },
  listAllCdds: async (params = {}) => {
    const res = await api.get(CDD.LIST_ALL, {
      params: { page: 1, page_size: 100, ...params },
    });
    return res.items || [];
  },
  /** Load CDD by course_design_documents.id (not course id). */
  getCdd:        (cddId)          => api.get(CDD.GET(cddId)),
  /** Load pinned CDD for a course — pass course id from /workspace/{courseId}/... */
  getActiveCddForCourse: (courseId) => api.get(COURSES.ACTIVE_CDD(courseId)),
  generateCdd:   async (data)     => {
    const created = await api.post(CDD.GENERATE, mapGeneratePayload(data));
    if (created?.cdd_id) {
      return api.get(CDD.GET(created.cdd_id));
    }
    return created;
  },
  /**
   * Enqueue a block-wide CDD build (long-running → async). Returns a job handle
   * {job_id, status, poll_url}; poll getJobStatus until terminal, then reload
   * the CDD by the job's result entity id.
   */
  generateCddBlock: (data) => api.post(CDD.GENERATE_BLOCK, mapBlockPayload(data)),
  /** Shared job-status endpoint — same one the generate/import flows poll. */
  getJobStatus:  (jobId)          => api.get(GENERATE.JOB_STATUS(jobId), POLL_REQUEST_CONFIG),
  /**
   * The caller's in-flight block-wide CDD job for this course, or null. Used on
   * mount to reattach a reloaded page to a build already running (a cold Block 2
   * build takes minutes, and the poll chain only ever lived in browser memory).
   */
  getActiveBlockJob: (courseId)   => api.get(GENERATE.JOB_ACTIVE(courseId, 'cdd_block')),
  /** Day-level progress of an in-flight block build. */
  getBlockJobProgress: (jobId) => api.get(GENERATE.JOB_PROGRESS(jobId), POLL_REQUEST_CONFIG),
  getVersions:   (id)             => api.get(CDD.VERSIONS(id)),
  getVersion:    (id, v)          => api.get(CDD.GET_VERSION(id, v)),
  activateVersion: (id, version)  => api.post(CDD.ACTIVATE_VERSION(id, version)),
  commitVersion: (id, data)       => api.post(CDD.COMMIT_VERSION(id), {
    version_tag: data.version_tag || data.tag || 'v-next',
    full_content: data.full_content,
    sections: data.sections || {},
    change_reason: data.change_reason || data.reason || '',
  }),
  setActiveCdd:  async (cddId, courseId) => {
    await api.post(CDD.PIN(cddId), { course_id: courseId });
    return api.get(CDD.GET(cddId));
  },
  regenerateItem: (id, data)      => api.post(CDD.REGENERATE_ITEM(id), {
    section_key: data.sectionKey,
    section_content: data.sectionContent,
    item_index: data.itemIndex,
    feedback: data.feedback || '',
    model_choice: data.modelChoice,
  }),
  regenerateSection: (id, data)   => api.post(CDD.REGENERATE_SECTION(id), {
    section_key: data.sectionKey,
    feedback: data.feedback || '',
    model_choice: data.modelChoice,
  }),
  exportCdd:     (id, format)     => api.download(CDD.EXPORT(id), { params: { format } }),
};
