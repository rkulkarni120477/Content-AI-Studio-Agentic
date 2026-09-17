import { api } from '@services/apiClient';
import { GENERATE, BLOCKS } from '@services/endpoints';

function mapLaunchPayload(data) {
  return {
    course_id: data.course_id,
    project_id: data.project_id,
    cdd_id: data.cdd_id ?? null,
    blueprint_id: data.blueprint_id ?? null,
    component_value: data.component_value,
    component_label: data.component_label,
    component_type: data.component_type,
    prompt_name: data.prompt_name,
    prompt_id: data.prompt_id ?? undefined,
    model_choice: data.model_choice || 'GPT-5.6 Terra',
    target_audience: data.target_audience || '',
    expert_domain: data.expert_domain || '',
    audience_category: data.audience_category || 'Professional/Corporate',
    extra_instructions: data.extra_instructions || '',
    // Day-scoped structured grounding (block+day) -- previously stripped by
    // this whitelist even when the caller supplied them, so Generate always
    // fell through to the free-text blob query. `block` is optional: the
    // server derives it from the course/CDD title when omitted.
    block: data.block || undefined,
    day: data.day ?? undefined,
    context_document_names: data.context_document_names || [],
    supplementary_files: data.supplementary_files || [],
    assessment_override: Boolean(data.assessment_override),
  };
}

export const generateService = {
  launch: (data) => api.post(GENERATE.LAUNCH, mapLaunchPayload(data)),

  getJobStatus: (jobId) => api.get(GENERATE.JOB_STATUS(jobId)),

  cancelJob: (jobId) => api.delete(GENERATE.JOB_CANCEL(jobId)),

  getGeneration: (id) => api.get(GENERATE.GET(id)),

  getBlock: (blockId) => api.get(BLOCKS.GET(blockId)),

  getGenerationBlocks: async (generationId) => {
    const res = await api.get(BLOCKS.LIST(generationId), { params: { page: 1, page_size: 100 } });
    return res.items || [];
  },

  getModuleCompletion: (blueprintId) => api.get(GENERATE.MODULE_COMPLETION(blueprintId)),

  getCourseCompletion: (courseId, cddId) => api.get(
    GENERATE.COURSE_COMPLETION(courseId),
    { params: cddId ? { cdd_id: cddId } : {} },
  ),
};
