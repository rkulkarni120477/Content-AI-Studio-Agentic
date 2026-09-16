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
      document_title: data.document_title || undefined,
      // Dropping this made every course-end item (capstone, appendix) persist
      // with a module number scraped out of its label instead of the 0 sentinel
      // the server reserves for them.
      is_course_end: Boolean(data.is_course_end),
      day_number: data.day_number ?? undefined,
      extra_instructions: data.extra_instructions || '',
      style_id: data.style_id ?? null,
      model_choice: data.model_choice || 'GPT-5.4',
      teacher_mode: Boolean(data.teacher_mode),
      system_prompt_override: data.system_prompt_override || undefined,
      user_prompt_override: data.user_prompt_override || undefined,
      prompt_id: data.prompt_id ?? undefined,
    };
    // Async (202 JobAccepted). Returns immediately with { job_id, status, status_url }.
    return api.post(BLUEPRINT.GENERATE, body);
  },

  /**
   * Import an existing Outline file (day-based DLU or module-based). Multipart
   * upload → the server extracts, detects the day/module from the file, then either
   * appends a new version to the existing Outline for that unit or creates a fresh
   * one, and pins it. Reloads the full blueprint by id (like generateBlueprint) so
   * the display gets the same shape a generated Outline has. `unitKind`/`unitNumber`
   * are only the dropdown hint — the file wins when it names a unit.
   */
  importBlueprint: async ({ file, courseId, projectId, documentTitle, unitKind, unitNumber, cddId, modelChoice }, onProgress) => {
    const form = new FormData();
    form.append('file', file);
    form.append('course_id', String(courseId));
    form.append('project_id', String(projectId));
    if (documentTitle) form.append('document_title', documentTitle);
    if (unitKind) form.append('unit_kind', unitKind);
    if (unitNumber != null && unitNumber !== '') form.append('unit_number', String(unitNumber));
    if (cddId != null) form.append('cdd_id', String(cddId));
    if (modelChoice) form.append('model_choice', modelChoice);
    const created = await api.upload(BLUEPRINT.IMPORT, form, onProgress);
    if (created?.blueprint_id) {
      const full = await api.get(BLUEPRINT.GET(created.blueprint_id));
      // The reloaded blueprint doesn't carry the import warnings — thread them
      // through so the thunk can warn on a degraded (e.g. single-section) import.
      if (created.import_warnings?.length) full.importWarnings = created.import_warnings;
      return full;
    }
    return created;
  },

  /**
   * Async Outline import. Same multipart form as importBlueprint, but the server
   * runs the extract + LLM restructure in a background job and returns a job
   * handle `{job_id, status, poll_url}` immediately — the caller polls getJobStatus
   * until terminal, then reloads the blueprint by the job's generation_id. This is
   * the timeout-proof path used by the UI (a slow file can't 504 the request).
   */
  importBlueprintAsync: async ({ file, courseId, projectId, documentTitle, unitKind, unitNumber, cddId, modelChoice }, onProgress) => {
    const form = new FormData();
    form.append('file', file);
    form.append('course_id', String(courseId));
    form.append('project_id', String(projectId));
    if (documentTitle) form.append('document_title', documentTitle);
    if (unitKind) form.append('unit_kind', unitKind);
    if (unitNumber != null && unitNumber !== '') form.append('unit_number', String(unitNumber));
    if (cddId != null) form.append('cdd_id', String(cddId));
    if (modelChoice) form.append('model_choice', modelChoice);
    return api.upload(BLUEPRINT.IMPORT_ASYNC, form, onProgress);
  },

  /** The caller's in-flight Outline-import job for this course, or null — lets a
   *  reloaded page reattach to an import already running server-side. */
  getActiveOutlineImportJob: (courseId) => api.get(GENERATE.JOB_ACTIVE(courseId, 'outline_import')),

  /**
   * Enqueue a block-wide Block Blueprint build (digest pipeline, async). Returns
   * a job handle {job_id, status, poll_url}; poll getJobStatus until terminal,
   * then reload the blueprint by the job's result entity id.
   *
   * style_id/prompt_id are carried for the same reason as the CDD twin — see
   * cddService.mapBlockPayload, where omitting them meant every block-wide
   * generation silently distilled its MAP guidance from the shipped file
   * template rather than the prompt the user selected. reference_document_ids is
   * carried for the same reason and is additive: pinned documents are digested on
   * top of the block's enumerated units, never instead of them.
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
    style_id: data.style_id ?? null,
    prompt_id: data.prompt_id || undefined,
    reference_document_ids: data.reference_document_ids || [],
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
    // The text being revised. Omitting it is what let "Regenerate Section"
    // return a fresh draft that had never seen the section it replaced.
    section_content: data.sectionContent || '',
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
