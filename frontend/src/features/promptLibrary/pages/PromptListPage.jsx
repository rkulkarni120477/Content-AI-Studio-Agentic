import { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  deletePrompt,
  downloadPromptsXlsx,
  duplicatePrompt,
  fetchMeta,
  fetchPrompts,
  markPromptUsed,
  restorePrompt,
} from '../api/prompts';
import { fetchPromptsByCourse } from '../api/flow';
import { plCourses } from '../paths';
import PromptCard from '../components/prompts/PromptCard';
import PromptListTable from '../components/prompts/PromptListTable';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { clusterOptions } from '../utils/clusters';
import { canManagePipelinePrompts, canManagePrompts } from '../utils/permissions';
import { CAS_CATEGORIES } from '../utils/prompt';

export default function PromptListPage() {
  const { user } = useAuth();
  const { show } = useToast();
  const canEdit = canManagePrompts(user);
  const canPipeline = canManagePipelinePrompts(user);

  const [prompts, setPrompts] = useState([]);
  const [meta, setMeta] = useState({ categories: [], tags: [] });
  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [tag, setTag] = useState('');
  const [wfState, setWfState] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [sort, setSort] = useState('updated');
  const [view, setView] = useState(() => localStorage.getItem('plib_view') || 'card');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  // Scope filter (Phase 11, doc §8; cluster-basis) — pipeline tab only:
  // narrow to the prompts a cluster/course actually uses (locks + inherited
  // defaults). Both selects derive from the one by-course batch call.
  const [scopeGroups, setScopeGroups] = useState([]);
  const [scopeCluster, setScopeCluster] = useState('');
  const [scopeCourse, setScopeCourse] = useState('');

  function buildFilterParams() {
    // Only CAS pipeline prompts are surfaced (Phase 12b) — the freeform
    // library rows stay in the DB but leave the console display entirely.
    const params = { roots_only: '1', kind: 'pipeline' };
    // Most specific scope wins server-side; send only one. "(No category)"
    // is a display bucket, not a lockable scope — it narrows only once a
    // course is picked.
    if (scopeCourse) params.course_id = scopeCourse;
    else if (scopeCluster && scopeCluster !== 'none') params.cluster_id = scopeCluster;
    if (q) params.q = q;
    // Workflows = the doc's six CAS category names ("Category" now means
    // cluster in the UI), resolved server-side to the (component_type,
    // variant) resolution keys — the cas_category param name is internal.
    if (category.startsWith('cas:')) params.cas_category = category.slice(4);
    if (tag) params.tag = tag;
    // Workflow status = the active version's state (doc §8).
    if (wfState) params.state = wfState;
    // Archived rows only on explicit opt-in (doc §9), admin-gated server-side.
    if (showArchived) params.include_archived = '1';
    if (sort) params.sort = sort;
    return params;
  }

  const load = useCallback(async () => {
    if (!canPipeline) return;
    setLoading(true);
    try {
      const list = await fetchPrompts(buildFilterParams());
      setPrompts(list);
    } catch {
      setPrompts([]);
      show('Could not load prompts.');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, category, tag, wfState, showArchived, sort, scopeCluster, scopeCourse]);

  useEffect(() => {
    void fetchMeta().then(setMeta).catch(() => {});
  }, []);

  // Scope selects are pipeline-manager-only; load the course-grouped view
  // once and derive the cluster and course option lists from it.
  useEffect(() => {
    if (!canPipeline || scopeGroups.length) return;
    void fetchPromptsByCourse().then(setScopeGroups).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canPipeline]);

  // Ambiguous cluster names (unique per project only) arrive disambiguated
  // with their project name — see utils/clusters.js.
  const scopeClusters = useMemo(() => clusterOptions(scopeGroups), [scopeGroups]);

  const scopeCourses = useMemo(() => {
    if (!scopeCluster) return [];
    return scopeGroups
      .filter((g) => (scopeCluster === 'none'
        ? g.cluster_id == null
        : String(g.cluster_id) === String(scopeCluster)))
      .map((g) => [g.course_id, g.course_name]);
  }, [scopeGroups, scopeCluster]);

  useEffect(() => {
    setScopeCourse('');
  }, [scopeCluster]);

  useEffect(() => {
    void load();
  }, [load]);

  function setViewMode(v) {
    setView(v);
    localStorage.setItem('plib_view', v);
  }

  async function handleCopy(id, content) {
    await navigator.clipboard.writeText(content);
    await markPromptUsed(id);
    show('Prompt copied to clipboard ✓');
  }

  async function handleDuplicate(id) {
    await duplicatePrompt(id);
    show('Prompt duplicated!');
    void load();
  }

  async function handleDelete(id) {
    if (!window.confirm('Delete this prompt?')) return;
    await deletePrompt(id);
    show('Prompt deleted.');
    void load();
  }

  async function handleRestore(id) {
    try {
      await restorePrompt(id);
      show('Prompt restored ♻ — available again everywhere.');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Restore failed');
    }
    void load();
  }

  function filterByTag(t) {
    setTag(t);
  }

  async function handleExportXlsx() {
    if (loading || exporting) return;
    setExporting(true);
    try {
      await downloadPromptsXlsx(buildFilterParams());
      show('Excel export downloaded.');
    } catch {
      show('Export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  }

  if (!canPipeline) {
    // Readers browse CAS prompts through the Courses view; the
    // freeform library list is retired from display (Phase 12b).
    return <Navigate to={plCourses} replace />;
  }

  return (
    <>
      <div className="library-toolbar">
        <div className="toolbar">
          <div className="search-wrap">
            <input
              type="text"
              placeholder="Search prompts…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Search prompts"
            />
            <svg className="search-ico" width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden="true">
              <circle cx="6.5" cy="6.5" r="5" stroke="currentColor" strokeWidth="1.5" />
              <line x1="10.354" y1="10.354" x2="13.5" y2="13.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </div>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All Workflows</option>
            {/* The doc's six CAS workflow categories — structural (component/
                variant-backed), never freeform strings. Labeled "Workflow"
                in the UI; "Category" is the cluster level's display name. */}
            {CAS_CATEGORIES.map((c) => (
              <option key={`cas:${c}`} value={`cas:${c}`}>
                {c}
              </option>
            ))}
          </select>
          {canPipeline && (
            <>
              <select
                value={scopeCluster}
                onChange={(e) => setScopeCluster(e.target.value)}
                title="Show the prompts this category uses (locks + inherited defaults)"
              >
                <option value="">All Categories</option>
                {scopeClusters.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
              <select
                value={scopeCourse}
                onChange={(e) => setScopeCourse(e.target.value)}
                disabled={!scopeCluster}
                title="Narrow to one course's effective prompt set"
              >
                <option value="">All Courses</option>
                {scopeCourses.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            </>
          )}
          <select value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">All Tags</option>
            {meta.tags.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select
            value={wfState}
            onChange={(e) => setWfState(e.target.value)}
            title="Filter by the active version's workflow status"
          >
            <option value="">Any Status</option>
            <option value="draft">Draft</option>
            <option value="in_review">In review</option>
            <option value="approved">Approved</option>
            <option value="active">Active (deployed)</option>
          </select>
          <label
            style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '.8rem', cursor: 'pointer' }}
            title="Archived prompts are excluded from generation and all pickers; show them here to inspect or restore"
          >
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            🗄 Archived
          </label>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="updated">Recently Updated</option>
            <option value="created">Recently Created</option>
            <option value="title">A → Z</option>
          </select>
          <div className="view-toggle">
            <button type="button" className={view === 'card' ? 'active' : ''} onClick={() => setViewMode('card')}>
              ⊞ Cards
            </button>
            <button type="button" className={view === 'list' ? 'active' : ''} onClick={() => setViewMode('list')}>
              ☰ List
            </button>
          </div>
          <span className="count">
            {prompts.length} prompt{prompts.length !== 1 ? 's' : ''}
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={handleExportXlsx}
            disabled={loading || exporting || prompts.length === 0}
            title="Download filtered prompts as an Excel workbook (includes full prompt text)"
          >
            {exporting ? 'Exporting…' : '⬇ Export XLSX'}
          </button>
        </div>
      </div>

      <div className="page-card">
        {loading ? (
          <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 48 }}>Loading…</p>
        ) : !prompts.length ? (
          <div className="empty">
            <div className="big">📭</div>
            <p>No prompts found. Try adjusting the search or filters.</p>
          </div>
        ) : view === 'list' ? (
          <PromptListTable
            prompts={prompts}
            isAdmin={canEdit}
            onCopy={handleCopy}
            onDelete={handleDelete}
            onRestore={handleRestore}
            onTagClick={filterByTag}
          />
        ) : (
          <div className="grid">
            {prompts.map((p) => (
              <PromptCard
                key={p.id}
                prompt={p}
                isAdmin={canEdit}
                onCopy={(id) => void handleCopy(id, p.content)}
                onDuplicate={handleDuplicate}
                onDelete={handleDelete}
                onRestore={handleRestore}
                onTagClick={filterByTag}
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
