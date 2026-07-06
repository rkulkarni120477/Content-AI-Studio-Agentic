import { useCallback, useEffect, useState } from 'react';
import {
  deletePrompt,
  duplicatePrompt,
  fetchMeta,
  fetchPrompts,
  markPromptUsed,
} from '../api/prompts';
import { listProjects, listProjectCourses } from '../api/flow';
import PromptCard from '../components/prompts/PromptCard';
import PromptListTable from '../components/prompts/PromptListTable';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { exportPromptsCsv } from '../utils/csvExport';
import { canManagePipelinePrompts, canManagePrompts } from '../utils/permissions';

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
  const [sort, setSort] = useState('updated');
  const [view, setView] = useState(() => localStorage.getItem('plib_view') || 'card');
  // Kind facet (Decision 1): non-admins never get a pipeline tab — the server
  // strips pipeline rows from their responses regardless; this is UI-side
  // defense-in-depth.
  const [kindTab, setKindTab] = useState('library');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  // Scope filter (Phase 11, doc §8) — pipeline tab only: narrow to the
  // prompts a project/course actually uses (locks + inherited defaults).
  const [scopeProjects, setScopeProjects] = useState([]);
  const [scopeCourses, setScopeCourses] = useState([]);
  const [scopeProject, setScopeProject] = useState('');
  const [scopeCourse, setScopeCourse] = useState('');

  function buildFilterParams() {
    const params = { roots_only: '1' };
    if (canPipeline && kindTab === 'pipeline') {
      params.kind = 'pipeline';
      // Most specific scope wins server-side; send only one.
      if (scopeCourse) params.course_id = scopeCourse;
      else if (scopeProject) params.project_id = scopeProject;
    }
    if (q) params.q = q;
    if (category) params.category = category;
    if (tag) params.tag = tag;
    if (sort) params.sort = sort;
    return params;
  }

  const load = useCallback(async () => {
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
  }, [q, category, tag, sort, kindTab, scopeProject, scopeCourse]);

  useEffect(() => {
    void fetchMeta().then(setMeta).catch(() => {});
  }, []);

  // Load the project list once the pipeline tab is first opened.
  useEffect(() => {
    if (kindTab !== 'pipeline' || !canPipeline || scopeProjects.length) return;
    void listProjects().then(setScopeProjects).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kindTab]);

  useEffect(() => {
    setScopeCourse('');
    setScopeCourses([]);
    if (!scopeProject) return;
    void listProjectCourses(scopeProject).then(setScopeCourses).catch(() => {});
  }, [scopeProject]);

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
    show('Copied! Paste it into ChatGPT ✓');
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

  function filterByTag(t) {
    setTag(t);
  }

  async function handleExportCsv() {
    if (loading || exporting) return;
    setExporting(true);
    try {
      const list = await fetchPrompts(buildFilterParams());
      if (!list.length) {
        show('No prompts to export for the current filters.');
        return;
      }
      exportPromptsCsv(list);
      show(`Exported ${list.length} prompt${list.length !== 1 ? 's' : ''} to CSV.`);
    } catch {
      show('Export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      <div className="library-toolbar">
        <div className="toolbar">
          {canPipeline && (
            <div className="view-toggle" title="Pipeline prompts drive live generation (admin)">
              <button
                type="button"
                className={kindTab === 'library' ? 'active' : ''}
                onClick={() => setKindTab('library')}
              >
                Library
              </button>
              <button
                type="button"
                className={kindTab === 'pipeline' ? 'active' : ''}
                onClick={() => setKindTab('pipeline')}
              >
                ⚙ Pipeline
              </button>
            </div>
          )}
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
            {meta.categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          {canPipeline && kindTab === 'pipeline' && (
            <>
              <select
                value={scopeProject}
                onChange={(e) => setScopeProject(e.target.value)}
                title="Show the prompts this project uses (locks + inherited defaults)"
              >
                <option value="">All Projects</option>
                {scopeProjects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <select
                value={scopeCourse}
                onChange={(e) => setScopeCourse(e.target.value)}
                disabled={!scopeProject}
                title="Narrow to one course's effective prompt set"
              >
                <option value="">All Courses</option>
                {scopeCourses.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
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
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="updated">Recently Updated</option>
            <option value="created">Recently Created</option>
            <option value="title">A → Z</option>
            <option value="category">Category</option>
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
            disabled={loading || exporting || prompts.length === 0}
            title="Download filtered prompts as CSV (includes full prompt text)"
          >
            {exporting ? 'Exporting…' : '⬇ Export CSV'}
          </button>
        </div>
      </div>

      <div className="page-card">
        {loading ? (
          <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 48 }}>Loading…</p>
        ) : !prompts.length ? (
          <div className="empty">
            <div className="big">📭</div>
            <p>No prompts found.{canEdit ? ' Add your first one!' : ''}</p>
          </div>
        ) : view === 'list' ? (
          <PromptListTable
            prompts={prompts}
            isAdmin={canEdit}
            onCopy={handleCopy}
            onDelete={handleDelete}
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
                onTagClick={filterByTag}
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
