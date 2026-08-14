import { api } from '@services/apiClient';
import { BLUEPRINT, GENERATE } from '@services/endpoints';
// Poll requests carry their own short timeout instead of the app-wide 120s one —
// see blockJob.POLL_REQUEST_TIMEOUT_MS for why that default was the wrong tool here.
import { POLL_REQUEST_CONFIG } from '@features/shared/blockJob';

export const blueprintService = {
  listBlueprints: async ({ courseId, projectId, includeArchived = false } = {}) => {
    const res = await api.get(BLUEPRINT.LIST, {
      params: {
        course_id: courseId,
        project_id: projectId,
        include_archived: includeArchived || undefined,
        page: 1,
        page_size: 100,
      },
    });
    return res.items || [];
  },

  /** All blueprints — matches Streamlit generate page override dropdown. */
  listAllBlueprints: async () => {
    const pageSize = 100;
    let page = 1;
    let items = [];
    let total = 0;
    do {
      const res = await api.get(BLUEPRINT.LIST, {
        params: { page, page_size: pageSize },
      });
      const batch = res.items || [];
      items = items.concat(batch);
      total = res.total ?? items.length;
      page += 1;
    } while (items.length < total && page <= 20);
    return items;
  },

  getBlueprint: (id) => api.get(BLUEPRINT.GET(id)),

  /**
   * Archive a blueprint — reversible, nothing is deleted. See
   * cddService.archiveCdd for why `unpin` is a separate, deliberate step.
   */
  archiveBlueprint: (id, { unpin = false } = {}) =>
    api.delete(BLUEPRINT.ARCHIVE(id), { params: { unpin } }),
  restoreBlueprint: (id) => api.post(BLUEPRINT.RESTORE(id)),
  /** Irreversible, admin-only, and refused by the server if anything references it. */
  purgeBlueprint: (id) => api.delete(BLUEPRINT.PURGE(id)),
  bulkArchiveBlueprints: ({ ids, unpin = false, courseId, projectId }) =>
    api.post(BLUEPRINT.BULK_ARCHIVE, {
      ids,
      unpin,
      course_id: courseId ?? undefined,
      project_id: projectId ?? undefined,
    }),
  getBlueprintReferences: (id) => api.get(BLUEPRINT.REFERENCES(id)),

  generateBlueprint: async (data) => {
    const body = {
      course_id: data.course_id,
      project_id: data.project_id,
      cdd_id: data.cdd_id ?? null,
      selected_module: data.selected_module,
      day_number: data.day_number ?? undefined,
      extra_instructions: data.extra_instructions || '',
      style_id: data.style_id ?? null,
      model_choice: data.model_choice || 'GPT-5.4',
      teacher_mode: Boolean(data.teacher_mode),
      system_prompt_override: data.system_prompt_override || undefined,
      user_prompt_override: data.user_prompt_override || undefined,
      prompt_id: data.prompt_id ?? undefined,
    };
    const created = await api.post(BLUEPRINT.GENERATE, body);
    if (created?.blueprint_id) {
      return api.get(BLUEPRINT.GET(created.blueprint_id));
    }
    return created;
  },

  /**
   * Enqueue a block-wide Block Blueprint build (digest pipeline, async). Returns
   * a job handle {job_id, status, poll_url}; poll getJobStatus until terminal,
   * then reload the blueprint by the job's result entity id.
   */
  generateBlueprintBlock: (data) => api.post(BLUEPRINT.GENERATE_BLOCK, {
    deliverable: 'blueprint',
    block: data.block,
    course_id: data.course_id,
    project_id: data.project_id,
    course_title: data.course_title || '',
    document_title: data.document_title || undefined,
    quality_tier: data.quality_tier || undefined,
    model_choice: data.model_choice || undefined,
    extra_instructions: data.extra_instructions || '',
    cdd_id: data.cdd_id ?? undefined,
    target_audience: data.target_audience || '',
    expert_domain: data.expert_domain || '',
    estimated_duration_hours: data.estimated_duration_hours ?? undefined,
  }),
  /** Shared job-status endpoint — same one the generate/import flows poll. */
  getJobStatus: (jobId) => api.get(GENERATE.JOB_STATUS(jobId), POLL_REQUEST_CONFIG),
  /**
   * The caller's in-flight block-wide Blueprint job for this course, or null.
   * Lets a reloaded page reattach instead of orphaning a running build.
   */
  getActiveBlockJob: (courseId) => api.get(GENERATE.JOB_ACTIVE(courseId, 'blueprint_block')),
  /** Day-level progress of an in-flight block build. */
  getBlockJobProgress: (jobId) => api.get(GENERATE.JOB_PROGRESS(jobId), POLL_REQUEST_CONFIG),

  getVersions: (id) => api.get(BLUEPRINT.VERSIONS(id)),
  getVersion: (id, v) => api.get(BLUEPRINT.GET_VERSION(id, v)),

  activateVersion: (id, version) => api.post(BLUEPRINT.ACTIVATE_VERSION(id, version)),
  commitVersion: (id, data) => api.post(BLUEPRINT.COMMIT_VERSION(id), {
    version_tag: data.version_tag || data.tag || 'v-next',
    full_content: data.full_content,
    sections: data.sections || {},
    change_reason: data.change_reason || data.reason || '',
  }),

  regenerateItem: (id, data) => api.post(BLUEPRINT.REGENERATE_ITEM(id), {
    section_key: data.sectionKey,
    section_content: data.sectionContent,
    item_index: data.itemIndex,
    feedback: data.feedback || '',
    model_choice: data.modelChoice,
  }),
  regenerateSection: (id, data) => api.post(BLUEPRINT.REGENERATE_SECTION(id), {
    section_key: data.sectionKey,
    feedback: data.feedback || '',
    model_choice: data.modelChoice,
    teacher_mode: Boolean(data.teacherMode),
  }),

  pinBlueprint: async (bpId, courseId) => {
    await api.post(BLUEPRINT.PIN(bpId), { course_id: courseId });
    return api.get(BLUEPRINT.GET(bpId));
  },

  exportBlueprint: (id, format) => api.download(BLUEPRINT.EXPORT(id), { params: { format } }),
  exportModuleLessons: (id, format) => api.download(BLUEPRINT.EXPORT_LESSONS(id), { params: { format } }),
  getComponents: async (id) => {
    const res = await api.get(BLUEPRINT.PARSE_COMPONENTS(id));
    return res.components || res || [];
  },
};
