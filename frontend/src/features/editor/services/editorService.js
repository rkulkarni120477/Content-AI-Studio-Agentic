import { api } from '@services/apiClient';
import { ASSETS, BLOCKS, EXPORT, GENERATE, PLAGIARISM, PROMPTS, USERS, WORKFLOW } from '@services/endpoints';

async function listAllPages(fetchPage) {
  const pageSize = 100;
  let page = 1;
  let all = [];
  let total = Infinity;
  while (all.length < total && page <= 20) {
    const res = await fetchPage(page, pageSize);
    const items = res.items || [];
    total = res.total ?? items.length;
    all = all.concat(items);
    if (items.length < pageSize) break;
    page += 1;
  }
  return all;
}

export const editorService = {
  listGenerations: async ({
    courseId, projectId, blueprintId, cddId, pageSize = 500,
  } = {}) => {
    const base = { page: 1, page_size: Math.min(pageSize, 500) };
    if (courseId != null) base.course_id = courseId;
    if (projectId != null) base.project_id = projectId;

    const scoped = { ...base };
    if (blueprintId != null) scoped.blueprint_id = blueprintId;
    else if (cddId != null) scoped.cdd_id = cddId;

    const res = await api.get(GENERATE.LIST, { params: scoped });
    let items = res.items || [];
    if (items.length === 0 && (blueprintId != null || cddId != null)) {
      const fallback = await api.get(GENERATE.LIST, { params: base });
      items = fallback.items || [];
    }
    return items;
  },

  getGeneration: (id) => api.get(GENERATE.GET(id)),

  listBlocksForGeneration: async (generationId) => listAllPages((page, pageSize) =>
    api.get(BLOCKS.LIST(generationId), { params: { page, page_size: pageSize } }),
  ),

  listCourseBlocks: async (courseId) => listAllPages((page, pageSize) =>
    api.get(BLOCKS.LIST_COURSE(courseId), { params: { page, page_size: pageSize } }),
  ),

  searchBlocks: (query) => api.get(BLOCKS.SEARCH, { params: { q: query, limit: 100 } }),

  getBlock: (id) => api.get(BLOCKS.GET(id)),

  updateBlock: (id, data) => api.put(BLOCKS.UPDATE(id), {
    content: data.content,
    change_reason: data.change_reason || data.edit_reason || 'Manual edit',
  }),

  autosaveBlock: (id, content) => api.post(BLOCKS.AUTOSAVE(id), { content }),

  regenerateBlock: (id, data) => api.post(BLOCKS.REGENERATE(id), {
    model_choice: data.model_choice || 'GPT-5.4',
    feedback_instruction: data.feedback_instruction || data.instruction || '',
  }),

  regenerateBlockItem: (id, data) => api.post(BLOCKS.REGENERATE_ITEM(id), data),

  getBlockVersions: (id) => api.get(BLOCKS.VERSIONS(id)),

  getBlockVersion: (id, versionId) => api.get(BLOCKS.GET_VERSION(id, versionId)),

  restoreVersion: (blockId, versionId) => api.post(BLOCKS.RESTORE_VERSION(blockId, versionId)),

  createSnapshot: (id, label) => api.post(BLOCKS.SNAPSHOT(id), { label: label || 'Manual snapshot' }),

  scoreBlock: (id) => api.post(BLOCKS.SCORE(id)),

  validateGeneration: (generationId) => api.post(BLOCKS.VALIDATE_GEN(generationId)),

  validateCourse: (courseId) => api.post(BLOCKS.VALIDATE_COURSE(courseId)),

  rateBlock: (id, rating) => api.put(BLOCKS.RATING(id), { rating }),

  exportGeneration: (generationId, format, template) => api.download(EXPORT.GENERATION(generationId), {
    params: { format, template: template || 'default' },
  }),

  exportCourse: (courseId, format, template) => api.download(BLOCKS.EXPORT_COURSE(courseId), {
    params: { format, template: template || 'default' },
  }),

  cleanupAssets: (urls) => api.post(ASSETS.CLEANUP, { urls }),

  triggerPlagiarism: (blockId) => api.post(PLAGIARISM.SCAN(blockId)),

  getPlagiarismStatus: (blockId, reportId) => api.get(PLAGIARISM.STATUS(blockId, reportId)),

  listReviewers: () => api.get(USERS.REVIEWERS),

  getPromptByName: async (name) => {
    const res = await api.get(PROMPTS.LIST, { params: { search: name, page_size: 20 } });
    const items = res.items || res || [];
    return items.find((p) => p.name === name) || items[0] || null;
  },

  getPromptVersion: (promptId, version) => api.get(PROMPTS.VERSIONS(promptId)).then((vers) => {
    const list = Array.isArray(vers) ? vers : (vers?.items || []);
    return list.find((v) => v.version === version) || list.find((v) => v.is_active) || list[0];
  }),

  submitWorkflowAction: (blockId, action, data = {}) => {
    const endpoints = {
      submit: WORKFLOW.SUBMIT(blockId),
      approve: WORKFLOW.APPROVE(blockId),
      request_changes: WORKFLOW.REQUEST_CHANGES(blockId),
      reject: WORKFLOW.REJECT(blockId),
      publish: WORKFLOW.PUBLISH(blockId),
      archive: WORKFLOW.ARCHIVE(blockId),
    };
    const body = action === 'submit'
      ? { reviewer_username: data.reviewer_username || data.reviewer }
      : data;
    return api.post(endpoints[action], body);
  },
};

export function buildPromptMarkdown({
  projectName, clusterName, courseName, component, promptName, promptVersion,
  systemPrompt, userPrompt, extraInstructions,
}) {
  return [
    '# Prompt Export',
    '',
    `**Project:** ${projectName || '—'}`,
    `**Cluster:** ${clusterName || '—'}`,
    `**Title:** ${courseName || '—'}`,
    `**Component:** ${component || 'generate'}`,
    `**Prompt:** ${promptName || '—'} (${promptVersion || '—'})`,
    '',
    '## System Prompt',
    '',
    '```',
    systemPrompt || '(empty)',
    '```',
    '',
    '## User Prompt Template',
    '',
    '```',
    userPrompt || '(empty)',
    '```',
    '',
    '## Extra Instructions',
    '',
    extraInstructions || '(none)',
    '',
  ].join('\n');
}
