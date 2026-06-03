/** Resolve active project id from Redux (workspace may still be hydrating). */
export function resolveProjectId(getState) {
  const dash = getState()?.dashboard;
  return dash?.selectedProject?.id ?? dash?.selectedCourse?.project_id ?? null;
}
