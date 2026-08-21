import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  deletePrompt,
  duplicatePrompt,
  fetchMeta,
  fetchPrompts,
  fetchPromptUsage,
  markPromptUsed,
  restorePrompt,
} from '../api/prompts';
import { fetchPromptsByCourse } from '../api/flow';
import { useDebounce } from '@hooks/useDebounce';
import { plCourses } from '../paths';
import CompactSelect from '../components/CompactSelect';
import DeletePromptDialog from '../components/prompts/DeletePromptDialog';
import PromptCard from '../components/prompts/PromptCard';
import PromptListTable from '../components/prompts/PromptListTable';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { exportPromptsCsv } from '../utils/csvExport';
import { clusterOptions } from '../utils/clusters';
import { canManagePipelinePrompts, canManagePrompts } from '../utils/permissions';
import { CAS_CATEGORIES } from '../utils/prompt';

export default function PromptListPage() {
  const { user } = useAuth();
  const { show } = useToast();
  const canEdit = canManagePrompts(user);
  const canPipeline = canManagePipelinePrompts(user);
  // Deleting a pipeline row is the admin tier, not the manager tier the rest
  // of the library uses — the backend enforces the same split.
  const canDelete = canPipeline;

  const [prompts, setPrompts] = useState([]);
  const [meta, setMeta] = useState({ categories: [], tags: [] });
  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [tag, setTag] = useState('');
  const [wfState, setWfState] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [sort, setSort] = useState('updated');
  const [view, setView] = useState(() => localStorage.getItem('plib_view') || 'card');
  // Two loading tiers, so a filter change never blanks the results the user is
  // reading: `loading` covers the first load only (the AC's "display after the
  // loading process is completed"), `refreshing` every later refetch, during
  // which the previous rows stay on screen behind aria-busy.
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState(false);
  // Typing must not fire one full request per keystroke; the other filters are
  // discrete clicks and stay immediate.
  const qDebounced = useDebounce(q, 300);
  // Scope filter (Phase 11, doc §8; cluster-basis) — pipeline tab only:
  // narrow to the prompts a cluster/course actually uses (locks + inherited
  // defaults). Both selects derive from the one by-course batch call.
  const [scopeGroups, setScopeGroups] = useState([]);
  const [scopeCluster, setScopeCluster] = useState('');
  const [scopeCourse, setScopeCourse] = useState('');

  // Delete flow: open the dialog immediately, then fill in the usage check as
  // it lands, so the click feels instant but the confirm button cannot be
  // pressed before the blast radius is known.
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteUsage, setDeleteUsage] = useState(null);
  const [deleteChecking, setDeleteChecking] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  // Guards against a stale usage response landing on a newer dialog when the
  // user opens one row's dialog, cancels, and opens another's straight away.
  const usageReqRef = useRef(0);

  // Same guard for the list itself: every load is numbered and the in-flight
  // one is aborted when a newer one starts, so the rows on screen always belong
  // to the newest filter set — never to whichever response happened to land
  // last. The unmount cleanup bumps the counter so a cancelled load cannot
  // touch state afterwards.
  const loadReqRef = useRef(0);
  const loadAbortRef = useRef(null);
  const loadedOnceRef = useRef(false);

  // Bumped by every mutation (not by filtering), so the list view can refresh
  // its open follow-up panels without refetching them on each keystroke.
  const [refreshToken, setRefreshToken] = useState(0);

  function buildFilterParams() {
    // Only CAS pipeline prompts are surfaced (Phase 12b) — the freeform
    // library rows stay in the DB but leave the console display entirely.
    const params = { roots_only: '1', kind: 'pipeline' };
    // Most specific scope wins server-side; send only one. "(No cluster)"
    // is a display bucket, not a lockable scope — it narrows only once a
    // course is picked.
    if (scopeCourse) params.course_id = scopeCourse;
    else if (scopeCluster && scopeCluster !== 'none') params.cluster_id = scopeCluster;
    if (qDebounced) params.q = qDebounced;
    // Categories are the doc's six CAS names, resolved server-side to the
    // (component_type, variant) resolution keys.
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
    // Supersede whatever is in flight: its response is already irrelevant, and
    // leaving it running would waste a full list transfer.
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    const token = loadReqRef.current + 1;
    loadReqRef.current = token;
    const isCurrent = () => loadReqRef.current === token;

    if (loadedOnceRef.current) setRefreshing(true);
    else setLoading(true);
    try {
      const list = await fetchPrompts(buildFilterParams(), { signal: controller.signal });
      if (!isCurrent()) return;
      // Tolerate both list shapes: the endpoint returns a bare array, or
      // {items,total,…} if a caller ever opts into its page/limit params.
      setPrompts(Array.isArray(list) ? list : list?.items || []);
      // Only a SUCCESSFUL load retires the placeholder — if the first one
      // failed, the next attempt should still read as loading rather than as
      // an empty library.
      loadedOnceRef.current = true;
    } catch (err) {
      // An abort is this component cancelling itself, not a failure — it must
      // neither clear the rows nor raise a toast.
      if (controller.signal.aborted || err?.name === 'AbortError' || !isCurrent()) return;
      setPrompts([]);
      show('Could not load prompts.');
    } finally {
      if (isCurrent()) {
        setLoading(false);
        setRefreshing(false);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qDebounced, category, tag, wfState, showArchived, sort, scopeCluster, scopeCourse]);

  // Cancel an in-flight load on unmount, and bump the counter first so its
  // rejection can no longer reach setState.
  useEffect(() => () => {
    loadReqRef.current += 1;
    loadAbortRef.current?.abort();
  }, []);

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

  const clusterFilterOptions = useMemo(
    () => [
      { value: '', label: 'All Clusters' },
      ...scopeClusters.map(([id, name]) => ({ value: String(id), label: name })),
    ],
    [scopeClusters],
  );

  const scopeCourses = useMemo(() => {
    if (!scopeCluster) return [];
    return scopeGroups
      .filter((g) => (scopeCluster === 'none'
        ? g.cluster_id == null
        : String(g.cluster_id) === String(scopeCluster)))
      .map((g) => [g.course_id, g.course_name]);
  }, [scopeGroups, scopeCluster]);

  const courseFilterOptions = useMemo(
    () => [
      { value: '', label: 'All Titles' },
      ...scopeCourses.map(([id, name]) => ({ value: String(id), label: name })),
    ],
    [scopeCourses],
  );

  useEffect(() => {
    setScopeCourse('');
  }, [scopeCluster]);

  useEffect(() => {
    void load();
  }, [load]);

  // Reload after a write. Callers use this instead of load() so the token and
  // the list can never drift apart.
  function reloadAfterMutation() {
    setRefreshToken((n) => n + 1);
    void load();
  }

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
    reloadAfterMutation();
  }

  function requestDelete(prompt) {
    setDeleteTarget(prompt);
    setDeleteUsage(null);
    setDeleteError('');
    setDeleteChecking(true);
    const token = usageReqRef.current + 1;
    usageReqRef.current = token;
    void fetchPromptUsage(prompt.id)
      .then((usage) => {
        if (usageReqRef.current !== token) return;
        setDeleteUsage(usage);
      })
      .catch((err) => {
        if (usageReqRef.current !== token) return;
        setDeleteError(err instanceof Error ? err.message : 'Could not check prompt usage');
      })
      .finally(() => {
        if (usageReqRef.current === token) setDeleteChecking(false);
      });
  }

  function closeDelete() {
    // Bump the token so an in-flight usage check cannot repopulate a closed
    // dialog or leak into the next one.
    usageReqRef.current += 1;
    setDeleteTarget(null);
    setDeleteUsage(null);
    setDeleteChecking(false);
    setDeleteError('');
  }

  async function confirmDelete() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError('');
    try {
      // Force only what the user was actually shown. If the preflight failed
      // we send force=false and let the server decide — never unbind blind.
      await deletePrompt(deleteTarget.id, { force: Boolean(deleteUsage?.blocking) });
      const name = deleteTarget.title || deleteTarget.pipeline?.name || 'Prompt';
      show(`${name} archived — restore it any time from the 🗄 Archived filter.`);
      closeDelete();
      reloadAfterMutation();
    } catch (err) {
      // 409 means the preflight was stale: someone bound the prompt between
      // the check and the click. Re-render the dialog from the server's own
      // payload so the next press is an informed one, not a silent force.
      if (err?.status === 409 && err.usage) setDeleteUsage(err.usage);
      setDeleteError(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setDeleting(false);
    }
  }

  async function handleRestore(id) {
    try {
      await restorePrompt(id);
      show('Prompt restored ♻ — available again everywhere.');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Restore failed');
    }
    reloadAfterMutation();
  }

  function filterByTag(t) {
    setTag(t);
  }

  // Export exactly the rows on screen. `prompts` is always the newest completed
  // load for the current filters (and the button is disabled while a load is in
  // flight), so this needs no second full-list request — the list payload
  // already carries every column the CSV writes, content included.
  function handleExportCsv() {
    if (loading || refreshing || exporting) return;
    if (!prompts.length) {
      show('No prompts to export for the current filters.');
      return;
    }
    setExporting(true);
    try {
      exportPromptsCsv(prompts);
      show(`Exported ${prompts.length} prompt${prompts.length !== 1 ? 's' : ''} to CSV.`);
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
        <div className="toolbar toolbar--filters">
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
            <option value="">All Categories</option>
            {/* The doc's six CAS categories — structural (component/variant-
                backed), never freeform strings. */}
            {CAS_CATEGORIES.map((c) => (
              <option key={`cas:${c}`} value={`cas:${c}`}>
                {c}
              </option>
            ))}
          </select>
          {canPipeline && (
            <>
              <CompactSelect
                fitContent
                aria-label="Filter by cluster"
                title="Show the prompts this cluster uses (locks + inherited defaults)"
                value={scopeCluster}
                onChange={setScopeCluster}
                options={clusterFilterOptions}
              />
              <CompactSelect
                fitContent
                aria-label="Filter by title"
                title="Narrow to one title's effective prompt set"
                value={scopeCourse}
                onChange={setScopeCourse}
                options={courseFilterOptions}
                disabled={!scopeCluster}
              />
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
        </div>
        <div className="toolbar toolbar--meta">
          <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort prompts">
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
            onClick={handleExportCsv}
            disabled={loading || refreshing || exporting || prompts.length === 0}
            title="Download filtered prompts as CSV (includes full prompt text)"
          >
            {exporting ? 'Exporting…' : '⬇ Export CSV'}
          </button>
        </div>
      </div>

      {/* A refetch keeps the previous rows on screen (dimmed, aria-busy) instead
          of swapping them for the placeholder — changing a filter must not
          blank what the user is reading. Only the first load shows "Loading…". */}
      <div
        className="page-card"
        aria-busy={loading || refreshing}
        style={refreshing ? { opacity: 0.6, transition: 'opacity 120ms ease' } : undefined}
      >
        {loading ? (
          <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 48 }}>Loading…</p>
        ) : !prompts.length ? (
          <div className="empty">
            <div className="big">📭</div>
            <p>No prompt titles found for this tenant. Try adjusting the search or filters.</p>
          </div>
        ) : view === 'list' ? (
          <PromptListTable
            prompts={prompts}
            isAdmin={canEdit}
            canDelete={canDelete}
            refreshToken={refreshToken}
            onCopy={handleCopy}
            onDelete={requestDelete}
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
                canDelete={canDelete}
                onCopy={(id) => void handleCopy(id, p.content)}
                onDuplicate={handleDuplicate}
                onDelete={requestDelete}
                onRestore={handleRestore}
                onTagClick={filterByTag}
              />
            ))}
          </div>
        )}
      </div>

      <DeletePromptDialog
        open={Boolean(deleteTarget)}
        prompt={deleteTarget}
        usage={deleteUsage}
        checking={deleteChecking}
        deleting={deleting}
        error={deleteError}
        onCancel={closeDelete}
        onConfirm={confirmDelete}
      />
    </>
  );
}
