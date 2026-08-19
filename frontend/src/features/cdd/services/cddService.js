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
    // Optional: omitted when the form's duration box is left blank, so the prompt
    // says nothing about duration rather than asserting a default nobody chose.
    estimated_duration_hours: data.estimated_duration_hours ?? data.duration_hours ?? undefined,
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

  /**
   * Archive a CDD — reversible, nothing is deleted.
   *
   * `unpin` is a deliberate second step: the server refuses to archive the CDD a
   * course has pinned as active unless it is set, so nobody silently removes the
   * document the next generation depends on.
   */
  archiveCdd: (cddId, { unpin = false } = {}) =>
    api.delete(CDD.ARCHIVE(cddId), { params: { unpin } }),
  restoreCdd: (cddId) => api.post(CDD.RESTORE(cddId)),
  /** Irreversible, admin-only, and refused by the server if anything references it. */
  purgeCdd: (cddId) => api.delete(CDD.PURGE(cddId)),
  /**
   * Archive many at once. Explicit ids only — the server has no predicate form,
   * so a bad filter can never widen into a mass delete.
   */
  bulkArchiveCdds: ({ ids, unpin = false, courseId, projectId }) =>
    api.post(CDD.BULK_ARCHIVE, {
      ids,
      unpin,
      course_id: courseId ?? undefined,
      project_id: projectId ?? undefined,
    }),
  getCddReferences: (cddId) => api.get(CDD.REFERENCES(cddId)),
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
   * Import an existing Blueprint/CDD file. Multipart upload → the server extracts,
   * normalizes into the worksheet shape, persists as a normal CDD and pins it.
   * Reloads the full CDD by id (like generateCdd) so the display accordion gets
   * the same shape a generated CDD has.
   */
  importCdd:     async ({ file, courseId, projectId, courseTitle, documentTitle, modelChoice }, onProgress) => {
    const form = new FormData();
    form.append('file', file);
    form.append('course_id', String(courseId));
    form.append('project_id', String(projectId));
    if (courseTitle) form.append('course_title', courseTitle);
    if (documentTitle) form.append('document_title', documentTitle);
    if (modelChoice) form.append('model_choice', modelChoice);
    const created = await api.upload(CDD.IMPORT, form, onProgress);
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
    // Omitted when the caller has no opinion, so the backend keeps inferring
    // from the instruction rather than being told "do not search".
    ...(data.useSources === undefined ? {} : { use_sources: data.useSources }),
    model_choice: data.modelChoice,
  }),
  regenerateSection: (id, data)   => api.post(CDD.REGENERATE_SECTION(id), {
    section_key: data.sectionKey,
    feedback: data.feedback || '',
    model_choice: data.modelChoice,
  }),
  exportCdd:     (id, format)     => api.download(CDD.EXPORT(id), { params: { format } }),
};
