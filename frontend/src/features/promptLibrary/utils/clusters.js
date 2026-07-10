// Cluster filter options shared by the Library and Courses tabs.
//
// Cluster names are unique only WITHIN a project (Project → Cluster → Course;
// the startup migration auto-creates a "General" cluster per project), so a
// bare-name dropdown shows indistinguishable duplicates. Names shared across
// projects are therefore disambiguated with their project name.

// [id, label] options from /prompts/by-course groups. Unclustered courses get
// a trailing "(No category)" bucket — a display bucket, not a lockable scope.
export function clusterOptions(groups) {
  const seen = new Map(); // cluster_id -> {name, project}
  let unclustered = false;
  for (const g of groups) {
    if (g.cluster_id == null) unclustered = true;
    else if (!seen.has(g.cluster_id)) {
      seen.set(g.cluster_id, {
        name: g.cluster_name || `Category ${g.cluster_id}`,
        project: g.project_name || `Project ${g.project_id}`,
      });
    }
  }
  const counts = new Map();
  for (const { name } of seen.values()) counts.set(name, (counts.get(name) || 0) + 1);
  const items = [...seen.entries()].map(([id, { name, project }]) => [
    id,
    counts.get(name) > 1 ? `${name} — ${project}` : name,
  ]);
  items.sort((a, b) => a[1].localeCompare(b[1]));
  if (unclustered) items.push(['none', '(No category)']);
  return items;
}
