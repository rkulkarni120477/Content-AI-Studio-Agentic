// Base path where the Prompt Library feature is mounted in the host router.
// All internal links/navigation are built from this so the feature is relocatable.
export const PL_BASE = '/prompt-library';

export const plHome = PL_BASE;
export const plPrompt = (id) => `${PL_BASE}/prompts/${id}`;
export const plPromptEdit = (id) => `${PL_BASE}/prompts/${id}/edit`;
export const plPromptNew = `${PL_BASE}/prompts/new`;
export const plPromptNewChild = (parentId) => `${PL_BASE}/prompts/new?parentId=${parentId}`;
export const plRequests = `${PL_BASE}/requests`;
export const plRequestNew = `${PL_BASE}/requests/new`;
export const plAdminRequests = `${PL_BASE}/admin/requests`;
export const plAdminRequest = (id) => `${PL_BASE}/admin/requests/${id}`;
export const plAdminReviews = `${PL_BASE}/admin/reviews`;
export const plAdminAudit = `${PL_BASE}/admin/audit`;
