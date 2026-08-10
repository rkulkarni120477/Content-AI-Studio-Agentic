// Course-grouped prompt view (Phase 11, requirements-doc §2/§6.2/§8;
// Phase 12c folds in the Flow view's lock editor and retires that tab).
//
// Courses as collapsible groups; expanding one shows the prompt every
// pipeline category resolves to for that course. The same default prompt
// appears under every course that resolves it while remaining ONE master
// row — display is per-course, storage is deduplicated (the doc's §6.2).
// Resolution is server-computed (GET /api/v1/prompts/by-course) with the
// exact semantics generation uses: scope lock (course → cluster → project
// → global) → component default → shipped file template.
//
// Each slot's "Change…" editor binds/unbinds a scope lock (PromptFixing) —
// by reference, never a copy. Phase-appropriateness and the non-admins-
// only-approved rule are enforced server-side; errors surface verbatim.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  bindFixing,
  fetchPromptsByCourse,
  listComponentPrompts,
  unbindFixing,
} from '../api/flow';
import CompactSelect from '../components/CompactSelect';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { canManagePipelinePrompts } from '../utils/permissions';
import { clusterOptions } from '../utils/clusters';
import { componentCategoryLabel } from '../utils/prompt';
import { plPrompt } from '../paths';

const SOURCE_BADGES = {
  course_lock: { className: 'badge-global', text: '🔒 Title lock' },
  cluster_lock: { className: 'badge-global', text: '🔒 Cluster lock' },
  project_lock: { className: 'badge-global', text: '🔒 Project lock' },
  global_lock: { className: 'badge-global', text: '🔒 Global lock' },
  default: { className: 'badge-team', text: 'Default' },
  file: { className: 'badge-draft', text: 'File template' },
  builtin: { className: 'badge-draft', text: 'Built-in builder' },
};

const LOCK_SCOPES = {
  course_lock: 'course',
  cluster_lock: 'cluster',
  project_lock: 'project',
  global_lock: 'global',
};

// Mirror the server's _acceptable_variants exactly, so anything bound from a
// slot's editor is guaranteed to display in that slot and drive that slot's
// generation: NULL-variant slots take NULL-variant prompts only, the
// interactive slot is exact, authored-variant slots take their variant or
// the NULL base.
function poolForSlot(slot, items) {
  if (slot.component === 'generate' && slot.variant === 'interactive') {
    return items.filter((p) => p.variant === 'interactive');
  }
  if (!slot.variant) return items.filter((p) => !p.variant);
  return items.filter((p) => p.variant === slot.variant || !p.variant);
}

function promptOptionLabel(p) {
  return `${p.name}${p.variant ? ` (${p.variant})` : ''}${p.is_default ? ' ★default' : ''}`;
}

function SlotRow({ slot, group, canOpen, pool, busy, onLoadPool, onBind, onUnbind }) {
  const [editing, setEditing] = useState(false);
  const [promptId, setPromptId] = useState('');
  const [scope, setScope] = useState('course');
  const badge = SOURCE_BADGES[slot.source] || { className: 'badge-draft', text: slot.source };
  const p = slot.prompt;
  const label = p ? p.name + (p.variant ? ` (${p.variant})` : '') : null;
  const lockScope = LOCK_SCOPES[slot.source];
  const options = pool ? poolForSlot(slot, pool) : null;

  function toggleEditor() {
    const opening = !editing;
    setEditing(opening);
    if (opening) void onLoadPool(slot.component);
  }

  return (
    <div className="list-row" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ minWidth: 150, fontWeight: 600 }}>
          {componentCategoryLabel(slot.component, slot.variant)}
        </span>
        <span className={`badge ${badge.className}`}>{badge.text}</span>
        {p ? (
          canOpen ? (
            <Link to={plPrompt(p.id)}>{label}</Link>
          ) : (
            <span>{label}</span>
          )
        ) : (
          <span style={{ color: 'var(--muted)', fontSize: '.85rem' }}>
            {slot.source === 'builtin'
              ? 'No prompt row — the component builder generates this content.'
              : 'No prompt row — the shipped file template is used.'}
          </span>
        )}
        {p?.is_default && slot.source !== 'default' && (
          <span className="badge badge-team" title="This row is also the component default">
            ★ default row
          </span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {lockScope && (
            <button
              type="button"
              className="btn btn-ghost"
              style={{ padding: '2px 10px', fontSize: '.75rem' }}
              disabled={busy}
              title={`Remove the ${lockScope} lock — back to default resolution`}
              onClick={() => void onUnbind(group, slot)}
            >
              Unbind
            </button>
          )}
          <button
            type="button"
            className="btn btn-ghost"
            style={{ padding: '2px 10px', fontSize: '.75rem' }}
            onClick={toggleEditor}
          >
            {editing ? 'Close' : 'Change…'}
          </button>
        </span>
      </div>
      {editing && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="field" style={{ margin: 0, minWidth: 260 }}>
            <label style={{ fontSize: '.72rem' }}>Bind a prompt (by reference)</label>
            <select value={promptId} onChange={(e) => setPromptId(e.target.value)}>
              <option value="">
                {options === null
                  ? 'Loading…'
                  : options.length
                    ? `— choose a ${componentCategoryLabel(slot.component, slot.variant)} prompt —`
                    : '— no eligible prompts for this slot —'}
              </option>
              {(options || []).map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {promptOptionLabel(opt)}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label style={{ fontSize: '.72rem' }}>Scope</label>
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="course">Title</option>
              <option value="cluster" disabled={!group.cluster_id}>
                Cluster
              </option>
              <option value="project">Project</option>
              <option value="global">Global</option>
            </select>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            style={{ padding: '6px 14px' }}
            disabled={busy || !promptId}
            onClick={() => void onBind(group, slot, promptId, scope)}
          >
            Bind
          </button>
        </div>
      )}
    </div>
  );
}

export default function CoursePromptsPage() {
  const { user } = useAuth();
  const { show } = useToast();
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState({}); // course_id -> bool
  const [selCluster, setSelCluster] = useState('');
  const [pools, setPools] = useState({}); // component -> PromptListItem[]
  const [busy, setBusy] = useState({}); // slot key -> bool
  const poolRequests = useRef({}); // component -> true once requested
  const canOpen = canManagePipelinePrompts(user);

  const reload = useCallback(async () => {
    const items = await fetchPromptsByCourse();
    setGroups(items);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchPromptsByCourse()
      .then((items) => {
        if (alive) setGroups(items);
      })
      .catch(() => show('Could not load the title-grouped view.'))
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPool = useCallback(async (component) => {
    if (poolRequests.current[component]) return;
    poolRequests.current[component] = true;
    try {
      const items = await listComponentPrompts(component);
      setPools((prev) => ({ ...prev, [component]: items }));
    } catch {
      poolRequests.current[component] = false;
      setPools((prev) => ({ ...prev, [component]: [] }));
    }
  }, []);

  const slotKey = (g, slot) => `${g.course_id}:${slot.component}:${slot.variant || ''}`;

  async function handleBind(group, slot, promptId, scope) {
    const key = slotKey(group, slot);
    setBusy((prev) => ({ ...prev, [key]: true }));
    try {
      await bindFixing({
        component: slot.component,
        scopeLevel: scope,
        projectId: scope === 'project' ? group.project_id : null,
        clusterId: scope === 'cluster' ? group.cluster_id : null,
        courseId: scope === 'course' ? group.course_id : null,
        promptId: Number(promptId),
      });
      await reload();
      show(`Bound to the ${scope} scope ✓`);
    } catch (err) {
      show(err instanceof Error ? err.message : 'Bind failed');
    } finally {
      setBusy((prev) => ({ ...prev, [key]: false }));
    }
  }

  async function handleUnbind(group, slot) {
    const scope = LOCK_SCOPES[slot.source];
    if (!scope) return;
    const key = slotKey(group, slot);
    setBusy((prev) => ({ ...prev, [key]: true }));
    try {
      await unbindFixing({
        component: slot.component,
        scopeLevel: scope,
        projectId: scope === 'project' ? group.project_id : null,
        clusterId: scope === 'cluster' ? group.cluster_id : null,
        courseId: scope === 'course' ? group.course_id : null,
      });
      await reload();
      show('Lock removed — back to default resolution.');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Unbind failed');
    } finally {
      setBusy((prev) => ({ ...prev, [key]: false }));
    }
  }

  // Cluster-basis filter: courses group under their cluster; courses without
  // one land in a "(No cluster)" bucket so none disappear from the picker.
  // Cluster names are unique only within a project (every project gets an
  // auto-migrated "General"), so ambiguous names get their project appended.
  const clusters = useMemo(() => clusterOptions(groups), [groups]);

  const clusterSelectOptions = useMemo(
    () => [
      { value: '', label: 'All categories' },
      ...clusters.map(([id, name]) => ({ value: String(id), label: name })),
    ],
    [clusters],
  );

  const visible = selCluster
    ? groups.filter((g) => (selCluster === 'none'
        ? g.cluster_id == null
        : String(g.cluster_id) === String(selCluster)))
    : groups;

  return (
    <div>
      <div className="page-card" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: '1.05rem' }}>Prompts by title</h2>
        <p style={{ margin: '6px 0 10px', fontSize: '.85rem', color: 'var(--muted)' }}>
          Each title is listed with the prompt every category currently uses. A shared
          default can appear under many titles, but it is one prompt — edit it once and
          every title that uses it gets the update. To give a title its own prompt,
          use “Change…” on a row to lock a different prompt to that title, its cluster,
          its project, or globally — locks show a 🔒 badge, and the most specific one
          wins. Non-admins can only lock prompts whose active version is approved.
        </p>
        <div className="field field--compact">
          <label htmlFor="course-cluster-filter">Category</label>
          <CompactSelect
            id="course-cluster-filter"
            fitContent
            aria-label="Filter by category"
            value={selCluster}
            onChange={setSelCluster}
            options={clusterSelectOptions}
          />
        </div>
      </div>

      {loading && <p style={{ color: 'var(--muted)' }}>Loading…</p>}
      {!loading && visible.length === 0 && (
        <p style={{ color: 'var(--muted)' }}>
          {selCluster ? 'No titles found for this category.' : 'No prompt titles found for this tenant.'}
        </p>
      )}

      {visible.map((g) => {
        const open = !!expanded[g.course_id];
        return (
          <div key={g.course_id} className="page-card course-group-card">
            <button
              type="button"
              className="course-group-toggle"
              onClick={() => setExpanded((prev) => ({ ...prev, [g.course_id]: !open }))}
              aria-expanded={open}
            >
              <span style={{ fontWeight: 700 }}>{open ? '−' : '+'}</span>
              <span style={{ fontWeight: 700 }}>{g.course_name}</span>
              <span style={{ fontSize: '.8rem', color: 'var(--muted)' }}>
                {g.cluster_name ? `${g.cluster_name} · ` : ''}
                {g.project_name}
              </span>
            </button>
            {open && (
              <div className="course-group-body">
                {g.prompts.map((slot) => (
                  <SlotRow
                    key={slotKey(g, slot)}
                    slot={slot}
                    group={g}
                    canOpen={canOpen}
                    pool={pools[slot.component] ?? null}
                    busy={!!busy[slotKey(g, slot)]}
                    onLoadPool={loadPool}
                    onBind={handleBind}
                    onUnbind={handleUnbind}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
