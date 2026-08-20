import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchSummaryThunk,
  fetchProjectAnalyticsThunk,
  fetchGenerationHistoryThunk,
  fetchHistoryExtrasThunk, fetchFeedbackSummaryThunk, fetchFeedbackThunk,
  fetchReviewsThunk, fetchLlmCostThunk,
  fetchPermissionsOverviewThunk, fetchClearPresetsThunk, clearDatabaseThunk,
  fetchBudgetsThunk, upsertBudgetThunk, deleteBudgetThunk,
  fetchGenerationTraceThunk,
} from '@features/analytics/analyticsThunks';
import {
  selectSummary, selectProjectRows,
  selectGenHistory, selectPromptVersionHistory, selectDocUploadHistory,
  selectCddBpHistory, selectHistoryExtrasError, selectFeedbackSummary, selectFeedback, selectFeedbackScope,
  selectReviews, selectLlmCost,
  selectAnalyticsFilters, selectAnalyticsLoading, selectPermissionsOverview,
  selectClearPresets, selectBudgets, selectBudgetsError,
  selectGenerationTrace, selectGenerationTraceLoading, selectGenerationTraceError,
  setFilters, setFeedbackScope, clearGenerationTrace,
} from '@features/analytics/analyticsSlice';
import BudgetMeter from '@features/analytics/components/BudgetMeter/BudgetMeter';
import TraceViewer from '@features/analytics/components/TraceViewer/TraceViewer';
import { platformService } from '@features/platform/services/platformService';
import { analyticsService } from '@features/analytics/services/analyticsService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import {
  selectSelectedProject, selectSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import { DATE_RANGE_LABELS, DATE_RANGE_PRESETS } from '@utils/constants';
import { formatDate, formatDateTime, formatNumber } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import { XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, LineChart, Line } from 'recharts';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import Input from '@components/common/Input/Input';
import Table from '@components/common/Table/Table';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import { useLabels } from '@hooks/useLabels';
import styles from './AnalyticsPage.module.scss';

const MAIN_TABS = ['Dashboard', 'LLM Cost'];
const historyTabs = (L) => ['Generations', 'Registry Commits', 'Document Uploads', `${L.cdd} & ${L.blueprint} Log`];
const costTabs = (L) => ['By Model', 'Monthly Trend', 'By Project', `By ${L.title}`, 'By User'];
const platformBreakdownOptions = (L) => [
  { value: 'user', label: 'User' },
  { value: 'tenant', label: 'Tenant' },
  { value: 'course', label: L.title },
];
const FEEDBACK_FILTERS = [
  { value: 'learning', label: 'Learning signals only' },
  { value: 'one_time', label: 'One-time only' },
  { value: 'all', label: 'All signals' },
];
const DATE_RANGE_OPTIONS = Object.entries(DATE_RANGE_PRESETS).map(([, v]) => ({
  value: v, label: DATE_RANGE_LABELS[v],
}));

function stars(score) {
  if (!score) return '—';
  return '⭐'.repeat(Math.min(5, Math.max(0, score)));
}

export default function AnalyticsPage({ embedded = false }) {
  const dispatch = useAppDispatch();
  const L = useLabels();
  const HISTORY_TABS = historyTabs(L);
  const COST_TABS = costTabs(L);
  const PLATFORM_BREAKDOWN_OPTIONS = platformBreakdownOptions(L);
  const { isAdmin, isReviewer, isAuthor, user, canClearDb, hasPermission, isPlatformAdmin } = useAuth();

  const summary = useAppSelector(selectSummary);
  const projectRows = useAppSelector(selectProjectRows);
  const genHistory = useAppSelector(selectGenHistory);
  const promptVersionHistory = useAppSelector(selectPromptVersionHistory);
  const docUploadHistory = useAppSelector(selectDocUploadHistory);
  const cddBpHistory = useAppSelector(selectCddBpHistory);
  const historyExtrasError = useAppSelector(selectHistoryExtrasError);
  const feedbackSummary = useAppSelector(selectFeedbackSummary);
  const feedback = useAppSelector(selectFeedback);
  const feedbackScope = useAppSelector(selectFeedbackScope);
  const reviews = useAppSelector(selectReviews);
  const llmCost = useAppSelector(selectLlmCost);
  const permissionsOverview = useAppSelector(selectPermissionsOverview);
  const clearPresets = useAppSelector(selectClearPresets);
  const budgets = useAppSelector(selectBudgets);
  const budgetsError = useAppSelector(selectBudgetsError);
  const generationTrace = useAppSelector(selectGenerationTrace);
  const generationTraceLoading = useAppSelector(selectGenerationTraceLoading);
  const generationTraceError = useAppSelector(selectGenerationTraceError);
  const filters = useAppSelector(selectAnalyticsFilters);
  const isLoading = useAppSelector(selectAnalyticsLoading);
  const selProject = useAppSelector(selectSelectedProject);
  const selCourse = useAppSelector(selectSelectedCourse);

  const [activeTab, setActiveTab] = useState('Dashboard');
  const [historyTab, setHistoryTab] = useState(0);
  const [costTab, setCostTab] = useState(0);
  const [clearConfirmTag, setClearConfirmTag] = useState(null);
  const [showPermissions, setShowPermissions] = useState(false);
  const [permSubTab, setPermSubTab] = useState(0);
  const [showBudgetModal, setShowBudgetModal] = useState(false);
  const [budgetForm, setBudgetForm] = useState(null);
  const [budgetProjectOptions, setBudgetProjectOptions] = useState([]);
  const [budgetCourseOptions, setBudgetCourseOptions] = useState([]);
  const [budgetCoursesLoading, setBudgetCoursesLoading] = useState(false);
  const [showTraceModal, setShowTraceModal] = useState(false);
  const [platformBreakdown, setPlatformBreakdown] = useState('user');

  function loadDashboard() {
    dispatch(fetchSummaryThunk(filters));
    dispatch(fetchGenerationHistoryThunk(filters));
    dispatch(fetchHistoryExtrasThunk());
    dispatch(fetchFeedbackSummaryThunk());
    dispatch(fetchFeedbackThunk({ scope: feedbackScope === 'all' ? null : feedbackScope }));
    dispatch(fetchReviewsThunk());
    if (isPlatformAdmin) {
      dispatch(fetchPermissionsOverviewThunk());
      if (canClearDb) dispatch(fetchClearPresetsThunk());
      dispatch(fetchProjectAnalyticsThunk());
    }
  }

  async function openBudgetModal(policy) {
    setBudgetCourseOptions([]);
    setBudgetForm(policy ? {
      id: policy.id,
      scope: policy.scope,
      scope_id: policy.scope_id,
      period: policy.period,
      limit_type: policy.limit_type || 'usd',
      limit_usd: policy.limit_usd != null ? String(policy.limit_usd) : '',
      limit_tokens: policy.limit_tokens != null ? String(policy.limit_tokens) : '',
      warn_threshold_pct: String(policy.warn_threshold_pct),
      _courseProjectId: '',
    } : {
      scope: 'project', scope_id: '', period: 'monthly',
      limit_type: 'usd', limit_usd: '', limit_tokens: '', warn_threshold_pct: '80', _courseProjectId: '',
    });
    setShowBudgetModal(true);

    // Editing an existing title-scope policy — look up which project owns it
    // so the cascading picker can preselect it (scope_id alone doesn't say).
    if (policy?.scope === 'course') {
      try {
        const course = await dashboardService.getCourse(policy.scope_id);
        setBudgetForm((f) => (f ? { ...f, _courseProjectId: String(course.project_id) } : f));
      } catch { /* course lookup is a UX nicety only — the raw id is still saved either way */ }
    }
  }

  async function handleSaveBudget() {
    const result = await dispatch(upsertBudgetThunk({
      scope: budgetForm.scope,
      scope_id: budgetForm.scope_id.trim(),
      period: budgetForm.period,
      limit_type: budgetForm.limit_type,
      limit_usd: budgetForm.limit_type === 'usd' ? Number(budgetForm.limit_usd) : null,
      limit_tokens: budgetForm.limit_type === 'tokens' ? Number(budgetForm.limit_tokens) : null,
      warn_threshold_pct: Number(budgetForm.warn_threshold_pct),
    }));
    if (!result.error) setShowBudgetModal(false);
  }

  function openTraceModal(generationId) {
    setShowTraceModal(true);
    dispatch(fetchGenerationTraceThunk(generationId));
  }

  function closeTraceModal() {
    setShowTraceModal(false);
    dispatch(clearGenerationTrace());
  }

  const BUDGET_SCOPE_LABELS = { project: 'tenant', course: 'title', user: 'user' };

  function handleDeleteBudget(policy) {
    const scopeLabel = BUDGET_SCOPE_LABELS[policy.scope] || policy.scope;
    if (window.confirm(`Delete the budget policy for ${scopeLabel} ${policy.scope_id}?`)) {
      dispatch(deleteBudgetThunk(policy.id));
    }
  }

  async function handleClearPreset(tag) {
    const result = await dispatch(clearDatabaseThunk(tag));
    if (!result.error) {
      setClearConfirmTag(null);
      if (activeTab === 'Dashboard') loadDashboard();
    }
  }

  const clearPresetItems = (clearPresets || []).filter((p) => p.tag !== 'everything');
  const clearEverythingPreset = (clearPresets || []).find((p) => p.tag === 'everything');

  useEffect(() => {
    loadDashboard();
  }, [dispatch, filters.dateRange, isPlatformAdmin, canClearDb]);

  useEffect(() => {
    dispatch(fetchFeedbackThunk({ scope: feedbackScope === 'all' ? null : feedbackScope }));
  }, [dispatch, feedbackScope]);

  useEffect(() => {
    if (historyTab === 0) return;
    const needsRegistry = historyTab === 1 && promptVersionHistory.length === 0;
    const needsDocs = historyTab === 2 && docUploadHistory.length === 0;
    const needsCdd = historyTab === 3 && cddBpHistory.length === 0;
    if (needsRegistry || needsDocs || needsCdd) {
      dispatch(fetchHistoryExtrasThunk());
    }
  }, [dispatch, historyTab, promptVersionHistory.length, docUploadHistory.length, cddBpHistory.length]);

  // A regular user only ever sees their OWN budget — project/title budgets
  // are a platform-admin concern. A platform admin instead manages every
  // policy platform-wide.
  const hasBudgetContext = isPlatformAdmin || Boolean(user?.username);

  useEffect(() => {
    if (activeTab !== 'LLM Cost') return;
    if (isPlatformAdmin) { dispatch(fetchBudgetsThunk()); return; }
    if (user?.username) dispatch(fetchBudgetsThunk({ scope: 'user', scope_id: user.username }));
  }, [dispatch, activeTab, isPlatformAdmin, user?.username]);

  useEffect(() => {
    if (activeTab !== 'LLM Cost' || !isPlatformAdmin || budgetProjectOptions.length > 0) return;
    platformService.listTenants().then((tenants) => {
      setBudgetProjectOptions((tenants || []).map((t) => ({
        value: String(t.id),
        label: t.slug ? `${t.name} (${t.slug})` : t.name,
      })));
    }).catch(() => {});
  }, [activeTab, isPlatformAdmin, budgetProjectOptions.length]);

  useEffect(() => {
    const projectId = budgetForm?._courseProjectId;
    if (budgetForm?.scope !== 'course' || !projectId) { setBudgetCourseOptions([]); return; }
    setBudgetCoursesLoading(true);
    analyticsService.getProjectCourses(projectId).then((courses) => {
      setBudgetCourseOptions((courses || []).map((c) => ({ value: String(c.id), label: c.name })));
    }).catch(() => setBudgetCourseOptions([])).finally(() => setBudgetCoursesLoading(false));
  }, [budgetForm?.scope, budgetForm?._courseProjectId]);

  useEffect(() => {
    if (activeTab === 'LLM Cost') dispatch(fetchLlmCostThunk(filters));
  }, [dispatch, activeTab, filters]);

  const monthlyChart = (llmCost?.monthly || []).map((r) => ({
    month: r.Month || r.month,
    cost: r['Cost ($)'] ?? r.cost ?? 0,
    tokens: r.Tokens ?? r.tokens ?? 0,
  }));
  const modelChart = (llmCost?.by_model || []).map((r) => ({
    model: r.Model || r.model,
    cost: r['Cost ($)'] ?? r.cost ?? 0,
  }));

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Analytics' }]} noPadding hideHeaderUser={embedded}>
      <div className={styles.pageWrap}>
        <SectionBadge
          icon="📊"
          title="Analytics & Observability"
          subtitle="Real-time metrics, prompt performance tracking, and full audit trails for every action on the platform."
        />
        {isAuthor && (
          <p className={styles.scopeCaption}>
            Personal usage summary — detailed cost breakdowns are available to Admin and Lead roles.
          </p>
        )}
        {!isAdmin && !isAuthor && selProject && (
          <p className={styles.scopeCaption}>
            Showing your metrics for project <strong>{selProject.name}</strong>
            {selCourse ? <> → title <strong>{selCourse.name}</strong></> : null}.
          </p>
        )}
        {isAdmin && <p className={styles.scopeCaption}>Admin view — cross-project metrics.</p>}

        <div className={styles.tabs} role="tablist">
          {MAIN_TABS.map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              className={`${styles.tab} ${activeTab === tab ? styles['tab--active'] : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab}
            </button>
          ))}
        </div>

        <div className={styles.globalFilter}>
          <Select
            options={DATE_RANGE_OPTIONS}
            value={filters.dateRange}
            onChange={(e) => dispatch(setFilters({ dateRange: e.target.value }))}
            wrapperClassName={styles.dateRangeSelect}
          />
          <Button variant="ghost" size="sm" onClick={loadDashboard}>Refresh</Button>
        </div>

        {/* ── Dashboard (Streamlit main sections) ── */}
        {activeTab === 'Dashboard' && (
          isLoading && !summary ? (
            <div className={styles.center}><Loader size="xl" /></div>
          ) : (
            <>
              <div className={styles.kpiGrid}>
                {[
                  { label: 'Generations', value: formatNumber(summary?.generations), icon: '🚀' },
                  { label: 'Content Blocks', value: formatNumber(summary?.blocks), icon: '🧩' },
                  { label: 'Prompt Assets', value: formatNumber(summary?.prompt_assets), icon: '📚' },
                  { label: 'Documents', value: formatNumber(summary?.documents), icon: '📄' },
                  { label: 'CDDs', value: formatNumber(summary?.cdds), icon: '📋' },
                  { label: L.blueprints, value: formatNumber(summary?.blueprints), icon: '🗂️' },
                ].map((kpi) => (
                  <div key={kpi.label} className={styles.kpiCard}>
                    <span className={styles.kpiCard__icon} aria-hidden="true">{kpi.icon}</span>
                    <div>
                      <div className={styles.kpiCard__value}>{kpi.value ?? '—'}</div>
                      <div className={styles.kpiCard__label}>{kpi.label}</div>
                    </div>
                  </div>
                ))}
              </div>

              {isPlatformAdmin && projectRows.length > 0 && (
                <section className={styles.card}>
                  <h3 className={styles.card__title}>📁 Project-Level Comparison</h3>
                  <Table
                    columns={[
                      { key: 'project_name', header: 'Project', sortable: true },
                      { key: 'client', header: 'Client' },
                      { key: 'generations', header: 'Generations', align: 'right' },
                      { key: 'blocks', header: 'Blocks', align: 'right' },
                      { key: 'cdds', header: 'CDDs', align: 'right' },
                      { key: 'blueprints', header: L.blueprints, align: 'right' },
                    ]}
                    rows={projectRows}
                    rowKey="project_id"
                    pagination={false}
                  />
                </section>
              )}

              <section className={styles.card}>
                <h3 className={styles.card__title}>📜 Interactive System History</h3>
                <div className={styles.subTabs}>
                  {HISTORY_TABS.map((t, i) => (
                    <button key={t} type="button" className={`${styles.subTab} ${historyTab === i ? styles['subTab--active'] : ''}`} onClick={() => setHistoryTab(i)}>
                      {t}
                    </button>
                  ))}
                </div>
                {historyExtrasError && (
                  <p className={styles.emptyHint} role="alert">
                    {historyExtrasError} — restart the API server if endpoints were recently added.
                  </p>
                )}
                {historyTab === 0 && (
                  genHistory.length > 0 ? (
                    <Table
                      columns={[
                        { key: 'id', header: 'ID' },
                        { key: 'topic', header: 'Topic' },
                        { key: 'prompt_name', header: 'Asset' },
                        { key: 'prompt_version', header: 'V' },
                        { key: 'cdd_label', header: 'CDD' },
                        { key: 'blueprint_label', header: 'Blueprint' },
                        { key: 'created_at', header: 'Time', render: (v) => formatDate(v) },
                        {
                          key: 'actions', header: '', render: (_, row) => (
                            <Button variant="ghost" size="sm" onClick={() => openTraceModal(row.id)}>View Trace</Button>
                          ),
                        },
                      ]}
                      rows={genHistory}
                      rowKey="id"
                      pagination={false}
                    />
                  ) : <p className={styles.emptyHint}>No generations yet.</p>
                )}
                {historyTab === 1 && (
                  promptVersionHistory.length > 0 ? (
                    <Table
                      columns={[
                        { key: 'prompt_id', header: 'Asset ID' },
                        { key: 'version', header: 'V' },
                        { key: 'notes', header: 'Notes' },
                        { key: 'created_at', header: 'Time', render: (v) => formatDateTime(v) },
                      ]}
                      rows={promptVersionHistory.map((r, i) => ({ ...r, id: `pv-${i}` }))}
                      rowKey="id"
                      pagination={false}
                    />
                  ) : <p className={styles.emptyHint}>No versions deployed yet.</p>
                )}
                {historyTab === 2 && (
                  docUploadHistory.length > 0 ? (
                    <Table
                      columns={[
                        { key: 'filename', header: 'Filename' },
                        { key: 'tag', header: 'Tag' },
                        { key: 'file_type', header: 'Type' },
                        { key: 'user', header: 'User' },
                        { key: 'created_at', header: 'Time', render: (v) => formatDateTime(v) },
                      ]}
                      rows={docUploadHistory.map((r, i) => ({ ...r, id: `du-${i}` }))}
                      rowKey="id"
                      pagination={false}
                    />
                  ) : <p className={styles.emptyHint}>No documents uploaded yet.</p>
                )}
                {historyTab === 3 && (
                  cddBpHistory.length > 0 ? (
                    <Table
                      columns={[
                        { key: 'event', header: 'Event' },
                        { key: 'actor', header: 'Actor' },
                        { key: 'details', header: 'Details' },
                        { key: 'created_at', header: 'Time', render: (v) => formatDateTime(v) },
                      ]}
                      rows={cddBpHistory.map((r, i) => ({ ...r, id: `cb-${i}` }))}
                      rowKey="id"
                      pagination={false}
                    />
                  ) : <p className={styles.emptyHint}>No CDD or Blueprint events yet.</p>
                )}
              </section>

              <section className={styles.card}>
                <h3 className={styles.card__title}>🧠 Feedback Library — Learning Signals</h3>
                <p className={styles.card__caption}>
                  Only signals marked as learning appear in regenerations. Filter below to explore all captured feedback.
                </p>
                <div className={styles.miniKpiRow}>
                  <div className={styles.miniKpi}><span>Total</span><strong>{feedbackSummary.total}</strong></div>
                  <div className={styles.miniKpi}><span>🧠 Learning</span><strong>{feedbackSummary.learning}</strong></div>
                  <div className={styles.miniKpi}><span>⚡ One-time</span><strong>{feedbackSummary.one_time}</strong></div>
                </div>
                <Select
                  label="Filter"
                  options={FEEDBACK_FILTERS}
                  value={feedbackScope}
                  onChange={(e) => dispatch(setFeedbackScope(e.target.value))}
                  wrapperClassName={styles.feedbackFilter}
                />
                {(feedback?.items?.length > 0) ? (
                  <Table
                    columns={[
                      { key: 'id', header: 'ID' },
                      { key: 'source', header: 'Source' },
                      { key: 'scope', header: 'Scope' },
                      { key: 'block_type', header: 'Block Type' },
                      { key: 'instruction', header: 'Instruction' },
                      { key: 'author', header: 'Author' },
                      { key: 'created_at', header: 'Date', render: (v) => formatDate(v) },
                    ]}
                    rows={feedback.items}
                    rowKey="id"
                    pagination={false}
                  />
                ) : (
                  <p className={styles.emptyHint}>No feedback signals yet. Use Regenerate or Save Edit in the Editor to capture feedback.</p>
                )}
              </section>

              <section className={styles.card}>
                <h3 className={styles.card__title}>📋 Review Analytics</h3>
                {reviews.total > 0 ? (
                  <>
                    <div className={styles.miniKpiRow}>
                      <div className={styles.miniKpi}><span>Total Reviews</span><strong>{reviews.total}</strong></div>
                      <div className={styles.miniKpi}><span>Approved</span><strong>{reviews.approved}/{reviews.total}</strong></div>
                      <div className={styles.miniKpi}><span>Avg Score</span><strong>{reviews.avg_score}/5</strong></div>
                    </div>
                    <Table
                      columns={[
                        { key: 'block_id', header: 'Block' },
                        { key: 'reviewer', header: 'Reviewer' },
                        { key: 'reviewer_role', header: 'Role' },
                        { key: 'score', header: 'Score', render: (v) => stars(v) },
                        { key: 'approved', header: 'Approved', render: (v) => (v ? '✅' : '❌') },
                        { key: 'comments', header: 'Comments' },
                        { key: 'created_at', header: 'Time', render: (v) => formatDate(v) },
                      ]}
                      rows={(reviews.items || []).map((r, i) => ({ ...r, id: `rev-${i}` }))}
                      rowKey="id"
                      pagination={false}
                    />
                  </>
                ) : (
                  <p className={styles.emptyHint}>No reviews submitted yet. Submit reviews in the Editor tab.</p>
                )}
              </section>

              {isPlatformAdmin && canClearDb && (
                <>
                  <section className={styles.card}>
                    <h3 className={styles.card__title}>🗑️ Clear Database</h3>
                    <p className={styles.card__caption}>
                      Permanently delete records from specific areas of the database. User accounts are never affected.
                      Each action requires a confirmation click.
                    </p>
                    <div className={styles.clearList}>
                      {clearPresetItems.map((preset) => (
                        <div key={preset.tag} className={styles.clearRow}>
                          <div className={styles.clearRow__body}>
                            <div className={styles.clearRow__label}>{preset.label}</div>
                            {preset.info && <p className={styles.clearRow__info}>{preset.info}</p>}
                          </div>
                          <div className={styles.clearRow__actions}>
                            {clearConfirmTag === preset.tag ? (
                              <>
                                <Button variant="primary" size="sm" onClick={() => handleClearPreset(preset.tag)}>
                                  ⚠️ Confirm Clear
                                </Button>
                                <Button variant="ghost" size="sm" onClick={() => setClearConfirmTag(null)}>
                                  Cancel
                                </Button>
                              </>
                            ) : (
                              <Button variant="ghost" size="sm" onClick={() => setClearConfirmTag(preset.tag)}>
                                🗑️ Clear
                              </Button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>

                  {clearEverythingPreset && (
                    <section className={styles.card}>
                      <h4 className={styles.sectionHeading}>⚠️ Clear Everything</h4>
                      <p className={styles.clearRow__info}>{clearEverythingPreset.info}</p>
                      {clearConfirmTag === 'everything' ? (
                        <div className={styles.clearEverythingConfirm}>
                          <p className={styles.clearEverythingWarn}>
                            This will permanently delete ALL data except user accounts. Are you sure?
                          </p>
                          <div className={styles.clearEverythingActions}>
                            <Button variant="primary" size="sm" onClick={() => handleClearPreset('everything')}>
                              ✅ Yes
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => setClearConfirmTag(null)}>
                              ❌ No
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={() => setClearConfirmTag('everything')}>
                          🗑️ Clear Everything
                        </Button>
                      )}
                    </section>
                  )}
                </>
              )}

              {isPlatformAdmin && (
              <section className={styles.card}>
                <button
                  type="button"
                  className={styles.permToggle}
                  onClick={() => setShowPermissions((v) => !v)}
                  aria-expanded={showPermissions}
                >
                  🔐 Role &amp; Permission Reference {showPermissions ? '▾' : '▸'}
                </button>
                {showPermissions && permissionsOverview && (
                  <>
                    <div className={styles.subTabs}>
                      {['My Permissions', hasPermission('users.view') ? 'Full Matrix' : null].filter(Boolean).map((t, i) => (
                        <button
                          key={t}
                          type="button"
                          className={`${styles.subTab} ${permSubTab === i ? styles['subTab--active'] : ''}`}
                          onClick={() => setPermSubTab(i)}
                        >
                          {t}
                        </button>
                      ))}
                    </div>
                    {permSubTab === 0 && (
                      <div className={styles.permGrid}>
                        <p className={styles.permRoleBadge}>Your role: <strong>{permissionsOverview.role_display}</strong></p>
                        {(permissionsOverview.categories || []).map((cat) => (
                          <div key={cat.category} className={styles.permCategory}>
                            <div className={styles.permCategory__title}>
                              {cat.icon} {cat.category} {cat.has_access ? '✓' : '—'}
                            </div>
                            {cat.has_access ? (
                              <ul className={styles.permList}>
                                {(cat.permissions || []).map((p) => (
                                  <li key={p.key}>{p.label}</li>
                                ))}
                              </ul>
                            ) : (
                              <p className={styles.emptyHint}>No access</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {permSubTab === 1 && permissionsOverview.matrix?.length > 0 && (
                      <Table
                        columns={[
                          { key: 'Category', header: 'Category' },
                          { key: 'Permission', header: 'Permission' },
                          { key: 'Description', header: 'Description' },
                          { key: 'Scope', header: 'Scope' },
                          { key: 'Admin', header: 'Admin' },
                          { key: 'Lead', header: 'Lead' },
                          { key: 'ID', header: 'ID' },
                        ]}
                        rows={permissionsOverview.matrix.map((r, i) => ({ ...r, id: `perm-${i}` }))}
                        rowKey="id"
                        pagination={false}
                      />
                    )}
                    {permSubTab === 1 && !hasPermission('users.view') && (
                      <p className={styles.emptyHint}>The full matrix is visible to Admin and Lead roles.</p>
                    )}
                  </>
                )}
              </section>
              )}
            </>
          )
        )}

        {/* ── LLM Cost Dashboard ── */}
        {activeTab === 'LLM Cost' && (
          <>
            <SectionBadge icon="💰" title="LLM Cost Dashboard" subtitle="Token usage, estimated cost, and latency across all AI generation calls." />
            {!llmCost ? (
              <div className={styles.center}><Loader size="lg" /></div>
            ) : (
              <>
                <div className={styles.kpiGrid}>
                  <div className={styles.kpiCard}><div className={styles.kpiCard__value}>{llmCost.summary?.total_calls ?? 0}</div><div className={styles.kpiCard__label}>Total Calls</div></div>
                  <div className={styles.kpiCard}><div className={styles.kpiCard__value}>{formatNumber(llmCost.summary?.total_tokens)}</div><div className={styles.kpiCard__label}>Total Tokens</div></div>
                  {(isAdmin || isReviewer) ? (
                    <>
                      <div className={styles.kpiCard}><div className={styles.kpiCard__value}>${(llmCost.summary?.total_cost ?? 0).toFixed(4)}</div><div className={styles.kpiCard__label}>Est. Cost (USD)</div></div>
                      <div className={styles.kpiCard}><div className={styles.kpiCard__value}>{llmCost.summary?.failed_calls ?? 0}</div><div className={styles.kpiCard__label}>Failed Calls</div></div>
                      <div className={styles.kpiCard}><div className={styles.kpiCard__value}>{Math.round(llmCost.summary?.avg_duration_ms ?? 0)} ms</div><div className={styles.kpiCard__label}>Avg Latency</div></div>
                    </>
                  ) : (
                    <>
                      <div className={styles.kpiCard}><div className={styles.kpiCard__value}>{formatNumber(llmCost.summary?.total_input_tokens)}</div><div className={styles.kpiCard__label}>Input Tokens</div></div>
                      <div className={styles.kpiCard}><div className={styles.kpiCard__value}>{formatNumber(llmCost.summary?.total_output_tokens)}</div><div className={styles.kpiCard__label}>Output Tokens</div></div>
                    </>
                  )}
                </div>

                {hasBudgetContext && (
                  <section className={styles.card}>
                    <div className={styles.card__header}>
                      <h3 className={styles.card__title}>💳 Budget Policies</h3>
                      {isPlatformAdmin && (
                        <Button variant="primary" size="sm" onClick={() => openBudgetModal(null)}>+ Set Budget</Button>
                      )}
                    </div>
                    {!isPlatformAdmin && (
                      <p className={styles.card__caption}>Your own personal AI budget.</p>
                    )}
                    {budgetsError && <p className={styles.emptyHint} role="alert">{budgetsError}</p>}
                    {budgets.length > 0 ? (
                      budgets.map((b) => (
                        <BudgetMeter
                          key={b.id}
                          policy={b}
                          onEdit={isPlatformAdmin ? openBudgetModal : undefined}
                          onDelete={isPlatformAdmin ? handleDeleteBudget : undefined}
                        />
                      ))
                    ) : (
                      <p className={styles.emptyHint}>
                        {isPlatformAdmin ? 'No budget policies set yet.' : 'No budget has been set for your account yet.'}
                      </p>
                    )}
                  </section>
                )}

                {(isAdmin || isReviewer) && (
                  <section className={styles.card}>
                    <div className={styles.subTabs}>
                      {COST_TABS.map((t, i) => (
                        <button key={t} type="button" className={`${styles.subTab} ${costTab === i ? styles['subTab--active'] : ''}`} onClick={() => setCostTab(i)}>{t}</button>
                      ))}
                    </div>
                    {costTab === 0 && modelChart.length > 0 && (
                      <>
                        <Table columns={Object.keys(llmCost.by_model[0] || {}).map((k) => ({ key: k, header: k }))} rows={llmCost.by_model} rowKey="Model" pagination={false} />
                        <ResponsiveContainer width="100%" height={220}><BarChart data={modelChart}><XAxis dataKey="model" tick={{ fontSize: 10 }} /><YAxis /><Tooltip /><Bar dataKey="cost" fill="#4f46e5" /></BarChart></ResponsiveContainer>
                      </>
                    )}
                    {costTab === 1 && monthlyChart.length > 0 && (
                      <>
                        <Table columns={Object.keys(llmCost.monthly[0] || {}).map((k) => ({ key: k, header: k }))} rows={llmCost.monthly} rowKey="Month" pagination={false} />
                        <ResponsiveContainer width="100%" height={220}><LineChart data={monthlyChart}><XAxis dataKey="month" /><YAxis /><Tooltip /><Line type="monotone" dataKey="cost" stroke="#4f46e5" /></LineChart></ResponsiveContainer>
                      </>
                    )}
                    {costTab === 2 && (isAdmin ? (
                      llmCost.by_project?.length > 0 ? <Table columns={Object.keys(llmCost.by_project[0] || {}).map((k) => ({ key: k, header: k }))} rows={llmCost.by_project} rowKey="Project" pagination={false} /> : <p className={styles.emptyHint}>No project-level data yet.</p>
                    ) : <p className={styles.emptyHint}>Project-level breakdown is available to Admin only.</p>)}
                    {costTab === 3 && (
                      llmCost.by_course?.length > 0 ? <Table columns={Object.keys(llmCost.by_course[0] || {}).map((k) => ({ key: k, header: k }))} rows={llmCost.by_course} rowKey="Course" pagination={false} /> : <p className={styles.emptyHint}>No title-level data yet.</p>
                    )}
                    {costTab === 4 && (isAdmin ? (
                      llmCost.by_user?.length > 0 ? <Table columns={Object.keys(llmCost.by_user[0] || {}).map((k) => ({ key: k, header: k }))} rows={llmCost.by_user} rowKey="User" pagination={false} /> : <p className={styles.emptyHint}>No per-user data yet.</p>
                    ) : <p className={styles.emptyHint}>Per-user breakdown is available to Admin only.</p>)}
                  </section>
                )}
                {!isAdmin && !isReviewer && <p className={styles.emptyHint}>Contact your Admin or Lead for cost breakdowns.</p>}

                {isPlatformAdmin && (() => {
                  const PLATFORM_ROWS = {
                    user: { rows: llmCost.platform_user_usage, rowKey: 'User' },
                    tenant: { rows: llmCost.platform_tenant_usage, rowKey: 'Project' },
                    course: { rows: llmCost.platform_course_usage, rowKey: 'Course' },
                  };
                  const { rows, rowKey } = PLATFORM_ROWS[platformBreakdown];
                  return (
                    <section className={styles.card}>
                      <div className={styles.card__header}>
                        <h3 className={styles.card__title}>🌐 Token Usage — Platform-Wide</h3>
                        <Select
                          label="Breakdown"
                          options={PLATFORM_BREAKDOWN_OPTIONS}
                          value={platformBreakdown}
                          onChange={(e) => setPlatformBreakdown(e.target.value)}
                          wrapperClassName={styles.dateRangeSelect}
                        />
                      </div>
                      <p className={styles.card__caption}>Every tenant, every {rowKey.toLowerCase()} — not scoped to your own project.</p>
                      {rows?.length > 0 ? (
                        <Table
                          columns={Object.keys(rows[0] || {}).map((k) => ({ key: k, header: k }))}
                          rows={rows}
                          rowKey={rowKey}
                          pagination
                          pageSize={10}
                        />
                      ) : <p className={styles.emptyHint}>No data yet.</p>}
                    </section>
                  );
                })()}
              </>
            )}
          </>
        )}

        {budgetForm && (
          <Modal
            open={showBudgetModal}
            onClose={() => setShowBudgetModal(false)}
            title={budgetForm.id ? 'Edit Budget Policy' : 'Set Budget Policy'}
            size="sm"
            footer={(
              <>
                <Button variant="ghost" onClick={() => setShowBudgetModal(false)}>Cancel</Button>
                <Button variant="primary" onClick={handleSaveBudget}>Save</Button>
              </>
            )}
          >
            <form className={styles.form}>
              <Select
                label="Scope"
                required
                disabled={Boolean(budgetForm.id)}
                options={[
                  { value: 'project', label: 'Tenant' },
                  { value: 'course', label: 'Title' },
                  { value: 'user', label: 'User' },
                ]}
                value={budgetForm.scope}
                onChange={(e) => setBudgetForm({ ...budgetForm, scope: e.target.value, scope_id: '', _courseProjectId: '' })}
              />
              {budgetForm.scope === 'project' && (
                <Select
                  label="Tenant"
                  required
                  disabled={Boolean(budgetForm.id)}
                  placeholder="Select a tenant…"
                  options={budgetProjectOptions}
                  value={budgetForm.scope_id}
                  onChange={(e) => setBudgetForm({ ...budgetForm, scope_id: e.target.value })}
                />
              )}
              {budgetForm.scope === 'course' && (
                <>
                  <Select
                    label="Tenant"
                    required
                    disabled={Boolean(budgetForm.id)}
                    placeholder="Select a tenant…"
                    options={budgetProjectOptions}
                    value={budgetForm._courseProjectId}
                    onChange={(e) => setBudgetForm({ ...budgetForm, _courseProjectId: e.target.value, scope_id: '' })}
                  />
                  <Select
                    label="Title"
                    required
                    disabled={Boolean(budgetForm.id) || !budgetForm._courseProjectId}
                    placeholder={budgetCoursesLoading ? 'Loading titles…' : 'Select a title…'}
                    options={budgetCourseOptions}
                    value={budgetForm.scope_id}
                    onChange={(e) => setBudgetForm({ ...budgetForm, scope_id: e.target.value })}
                  />
                </>
              )}
              {budgetForm.scope === 'user' && (
                <Input
                  label="Username"
                  required
                  disabled={Boolean(budgetForm.id)}
                  value={budgetForm.scope_id}
                  onChange={(e) => setBudgetForm({ ...budgetForm, scope_id: e.target.value })}
                />
              )}
              <Select
                label="Period"
                required
                options={[
                  { value: 'monthly', label: 'Monthly' },
                  { value: 'rolling', label: 'Rolling' },
                ]}
                value={budgetForm.period}
                onChange={(e) => setBudgetForm({ ...budgetForm, period: e.target.value })}
              />
              <Select
                label="Cap type"
                required
                disabled={Boolean(budgetForm.id)}
                options={[
                  { value: 'usd', label: 'USD budget' },
                  { value: 'tokens', label: 'Token limit' },
                ]}
                value={budgetForm.limit_type}
                onChange={(e) => setBudgetForm({ ...budgetForm, limit_type: e.target.value })}
              />
              {budgetForm.limit_type === 'tokens' ? (
                <Input
                  label="Token limit"
                  type="number"
                  min="1"
                  step="1"
                  required
                  value={budgetForm.limit_tokens}
                  onChange={(e) => setBudgetForm({ ...budgetForm, limit_tokens: e.target.value })}
                />
              ) : (
                <Input
                  label="Limit (USD)"
                  type="number"
                  min="0.01"
                  step="0.01"
                  required
                  value={budgetForm.limit_usd}
                  onChange={(e) => setBudgetForm({ ...budgetForm, limit_usd: e.target.value })}
                />
              )}
              <Input
                label="Warn threshold (%)"
                type="number"
                min="0"
                max="100"
                required
                value={budgetForm.warn_threshold_pct}
                onChange={(e) => setBudgetForm({ ...budgetForm, warn_threshold_pct: e.target.value })}
              />
            </form>
          </Modal>
        )}

        <Modal
          open={showTraceModal}
          onClose={closeTraceModal}
          title="Generation Trace"
          size="lg"
          footer={<Button variant="ghost" onClick={closeTraceModal}>Close</Button>}
        >
          {generationTraceLoading && <div className={styles.center}><Loader size="lg" /></div>}
          {generationTraceError && <p className={styles.emptyHint} role="alert">{generationTraceError}</p>}
          {generationTrace?.scope === 'module_reconstruction' && (
            <p className={styles.scopeCaption}>
              This item was imported, not AI-generated itself — showing the trace for the
              AI call that reconstructed its module's Blueprint from the imported content
              (shared by every lesson in that module).
            </p>
          )}
          {generationTrace && <TraceViewer observations={generationTrace.observations} />}
        </Modal>
      </div>
    </PageContainer>
  );
}
