import { useMemo } from 'react';
import { useAppSelector } from '@app/hooks';
import { selectSelectedProject } from '@features/dashboard/dashboardSlice';
import { buildLabels } from '@config/tenantLabels';

/**
 * The current tenant's display labels.
 *
 * A tenant IS a project (see `class Project` in promptops_app/database.py), so
 * the overrides ride along on the selected project — which every page in the
 * pipeline already loads via `dashboardService.getProject()`:
 *   - ClustersPage / CoursesPage fetch it when the URL's project differs
 *   - WorkspaceLayout re-fetches it on a hard refresh into /workspace/:id
 *
 * Outside any tenant context (login, platform dashboard) `selectedProject` is
 * null and this returns the default wording, so callers never need a guard.
 *
 * Usage:
 *   const L = useLabels();
 *   <h1>Select {L.title}</h1>          // "Select Block"
 *   <p>Generated {L.styles}</p>        // "Generated Design Guides"
 *   <p>…for every {L.titleLower}</p>   // "…for every block"
 */
export function useLabels() {
  const project = useAppSelector(selectSelectedProject);
  const overrides = project?.ui_labels;

  // Keyed on the serialized overrides so the label set is a stable reference
  // between renders — it flows into deps arrays and memoized children.
  const key = useMemo(() => JSON.stringify(overrides ?? {}), [overrides]);

  return useMemo(() => buildLabels(overrides), [key]); // eslint-disable-line react-hooks/exhaustive-deps
}

export default useLabels;
