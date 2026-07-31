import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchSummaryThunk, fetchAuditTrailThunk, fetchUsersThunk, createUserThunk,
  toggleUserActiveThunk, exportAuditThunk,
  fetchProjectAnalyticsThunk, fetchPromptPerfThunk,
  fetchQualityTrendsThunk, fetchGenerationHistoryThunk,
  fetchHistoryExtrasThunk, fetchFeedbackSummaryThunk, fetchFeedbackThunk,
  fetchReviewsThunk, fetchSystemLogsThunk, fetchLlmCostThunk,
  fetchPermissionsOverviewThunk, fetchClearPresetsThunk, clearDatabaseThunk,
  fetchAuditTrailFiltersThunk,
} from '@features/analytics/analyticsThunks';
import {
  selectSummary, selectProjectRows, selectPromptPerf, selectQualityRatings,
  selectGenHistory, selectPromptVersionHistory, selectDocUploadHistory,
  selectCddBpHistory, selectHistoryExtrasError, selectFeedbackSummary, selectFeedback, selectFeedbackScope,
  selectReviews, selectSystemLogs, selectLlmCost, selectAuditTrail, selectUsers,
  selectAnalyticsFilters, selectAnalyticsLoading, selectPermissionsOverview,
  selectClearPresets, selectAuditTrailLoading, selectAuditTrailError,
  selectAuditFilterOptions, selectAuditFilters,
  setFilters, setFeedbackScope, setAuditFilters,
} from '@features/analytics/analyticsSlice';
import {
  selectSelectedProject, selectSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import { createUserSchema } from '@utils/validation';
import { DATE_RANGE_LABELS, DATE_RANGE_PRESETS } from '@utils/constants';
import { formatDate, formatDateTime, formatNumber, formatTimestamp } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, LineChart, Line } from 'recharts';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import Input from '@components/common/Input/Input';
import Table from '@components/common/Table/Table';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import { useLabels } from '@hooks/useLabels';
import styles from './AnalyticsPage.module.scss';

const MAIN_TABS = ['Dashboard', 'LLM Cost', 'Audit Trail', 'User Management'];
const historyTabs = (L) => ['Generations', 'Registry Commits', 'Document Uploads', `${L.cdd} & ${L.blueprint} Log`];
const costTabs = (L) => ['By Model', 'Monthly Trend', 'By Project', `By ${L.title}`, 'By User'];
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

export default function AnalyticsPage() {
  const dispatch = useAppDispatch();
  const L = useLabels();
  const HISTORY_TABS = historyTabs(L);
  const COST_TABS = costTabs(L);
  const { isAdmin, isReviewer, isAuthor, user, canManageUsers, canClearDb, hasPermission } = useAuth();
  const canViewAudit = isAdmin || isReviewer;

  const summary = useAppSelector(selectSummary);
  const projectRows = useAppSelector(selectProjectRows);
  const promptPerf = useAppSelector(selectPromptPerf);
  const qualityRatings = useAppSelector(selectQualityRatings);
  const genHistory = useAppSelector(selectGenHistory);
  const promptVersionHistory = useAppSelector(selectPromptVersionHistory);
  const docUploadHistory = useAppSelector(selectDocUploadHistory);
  const cddBpHistory = useAppSelector(selectCddBpHistory);
  const historyExtrasError = useAppSelector(selectHistoryExtrasError);
  const feedbackSummary = useAppSelector(selectFeedbackSummary);
  const feedback = useAppSelector(selectFeedback);
  const feedbackScope = useAppSelector(selectFeedbackScope);
  const reviews = useAppSelector(selectReviews);
  const systemLogs = useAppSelector(selectSystemLogs);
  const llmCost = useAppSelector(selectLlmCost);
  const auditTrail = useAppSelector(selectAuditTrail);
  const users = useAppSelector(selectUsers);
  const permissionsOverview = useAppSelector(selectPermissionsOverview);
  const clearPresets = useAppSelector(selectClearPresets);
  const filters = useAppSelector(selectAnalyticsFilters);
  const isLoading = useAppSelector(selectAnalyticsLoading);
  const auditTrailLoading = useAppSelector(selectAuditTrailLoading);
  const auditTrailError = useAppSelector(selectAuditTrailError);
  const auditFilterOptions = useAppSelector(selectAuditFilterOptions);
  const auditFilters = useAppSelector(selectAuditFilters);
  const selProject = useAppSelector(selectSelectedProject);
  const selCourse = useAppSelector(selectSelectedCourse);

  const visibleMainTabs = MAIN_TABS.filter((t) => {
    if (isAuthor) return t === 'Dashboard' || t === 'LLM Cost';
    return (t !== 'Audit Trail' || canViewAudit) && (t !== 'User Management' || canManageUsers);
  });
  const [activeTab, setActiveTab] = useState('Dashboard');
  const [historyTab, setHistoryTab] = useState(0);
  const [costTab, setCostTab] = useState(0);
  const [systemLogPage, setSystemLogPage] = useState(1);
  const [auditPage, setAuditPage] = useState(1);
  const [showUserModal, setShowUserModal] = useState(false);
  const [toggleUserSelect, setToggleUserSelect] = useState('');
  const [clearConfirmTag, setClearConfirmTag] = useState(null);
  const [showPermissions, setShowPermissions] = useState(false);
  const [permSubTab, setPermSubTab] = useState(0);

  const userForm = useForm({ resolver: zodResolver(createUserSchema) });

  function loadDashboard() {
    dispatch(fetchSummaryThunk(filters));
    dispatch(fetchPromptPerfThunk(filters));
    dispatch(fetchQualityTrendsThunk(filters));
    dispatch(fetchGenerationHistoryThunk(filters));
    dispatch(fetchHistoryExtrasThunk());
    dispatch(fetchFeedbackSummaryThunk());
    dispatch(fetchFeedbackThunk({ scope: feedbackScope === 'all' ? null : feedbackScope }));
    dispatch(fetchReviewsThunk());
    dispatch(fetchSystemLogsThunk({ page: systemLogPage }));
    dispatch(fetchPermissionsOverviewThunk());
    if (canClearDb) dispatch(fetchClearPresetsThunk());
    if (isAdmin) dispatch(fetchProjectAnalyticsThunk());
  }

  function loadAuditTrail(page = auditPage) {
    dispatch(fetchAuditTrailThunk({
      page,
      pageSize: auditFilters.pageSize,
      filters: auditFilters,
    }));
  }

  function handleAuditPage(page) {
    setAuditPage(page);
    loadAuditTrail(page);
  }

  function updateAuditFilter(patch) {
    dispatch(setAuditFilters(patch));
    setAuditPage(1);
  }

  function handleSystemLogPage(page) {
    setSystemLogPage(page);
    dispatch(fetchSystemLogsThunk({ page }));
  }

  async function handleClearPreset(tag) {
    const result = await dispatch(clearDatabaseThunk(tag));
    if (!result.error) {
      setClearConfirmTag(null);
      if (activeTab === 'Dashboard') loadDashboard();
      else if (activeTab === 'Audit Trail') {
        loadAuditTrail(auditPage);
      } else {
        dispatch(fetchSystemLogsThunk({ page: systemLogPage }));
      }
    }
  }

  const toggleUserOptions = (users || [])
    .filter((u) => u.username !== user?.username)
    .map((u) => ({
      value: String(u.id),
      label: `${u.username} (${u.role_display || u.role})`,
    }));

  const selectedToggleUser = (users || []).find((u) => String(u.id) === toggleUserSelect);
  const toggleActionLabel = selectedToggleUser
    ? (selectedToggleUser.is_active !== false ? '🚫 Deactivate' : '✅ Reactivate')
    : null;

  const clearPresetItems = (clearPresets || []).filter((p) => p.tag !== 'everything');
  const clearEverythingPreset = (clearPresets || []).find((p) => p.tag === 'everything');

  async function handleToggleAccess() {
    if (!toggleUserSelect) return;
    const result = await dispatch(toggleUserActiveThunk({
      userId: Number(toggleUserSelect),
      isActive: selectedToggleUser?.is_active === false,
    }));
    if (!result.error) dispatch(fetchUsersThunk());
  }

  useEffect(() => {
    loadDashboard();
  }, [dispatch, filters.dateRange, isAdmin]);

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

  useEffect(() => {
    if (activeTab === 'LLM Cost') dispatch(fetchLlmCostThunk(filters));
    if (activeTab === 'Audit Trail' && canViewAudit) {
      dispatch(fetchAuditTrailFiltersThunk());
    }
    if (activeTab === 'User Management' && canManageUsers) {
      dispatch(fetchUsersThunk());
    }
  }, [dispatch, activeTab, filters, canViewAudit, isAdmin, canManageUsers]);

  useEffect(() => {
    if (activeTab !== 'Audit Trail' || !canViewAudit) return;
    loadAuditTrail(auditPage);
  }, [dispatch, auditFilters, auditPage, activeTab, canViewAudit]);

  useEffect(() => {
    if (toggleUserOptions.length > 0 && !toggleUserOptions.some((o) => o.value === toggleUserSelect)) {
      setToggleUserSelect(toggleUserOptions[0].value);
    }
  }, [toggleUserOptions, toggleUserSelect]);

  async function onCreateUser(data) {
    const result = await dispatch(createUserThunk(data));
    if (!result.error) {
      setShowUserModal(false);
      userForm.reset();
      dispatch(fetchUsersThunk());
    }
  }

  const qualityChartData = (qualityRatings || []).map((r, i) => ({ index: i + 1, rating: r }));
  const monthlyChart = (llmCost?.monthly || []).map((r) => ({
    month: r.Month || r.month,
    cost: r['Cost ($)'] ?? r.cost ?? 0,
    tokens: r.Tokens ?? r.tokens ?? 0,
  }));
  const modelChart = (llmCost?.by_model || []).map((r) => ({
    model: r.Model || r.model,
    cost: r['Cost ($)'] ?? r.cost ?? 0,
  }));

  const AUDIT_COLUMNS = [
    { key: 'created_at', header: 'When', render: (v) => formatTimestamp(v) },
    { key: 'actor', header: 'User' },
    { key: 'action', header: 'Action', render: (v, row) => `${row.icon || ''} ${v || ''}`.trim() },
    { key: 'label', header: 'Label' },
    { key: 'entity_type', header: 'Entity type', render: (v) => v || '—' },
    { key: 'entity_id', header: 'Entity ID', render: (v) => v || '—' },
    { key: 'project_id', header: 'Project', render: (v) => (v != null ? String(v) : '—') },
    { key: 'ip_address', header: 'IP', render: (v) => v || '—' },
  ];

  const actorOptions = [
    ...(isAdmin ? [{ value: '', label: 'All users' }] : []),
    ...(auditFilterOptions.actors || []).map((a) => ({ value: a, label: a })),
  ];
  const actionOptions = [
    { value: '', label: 'All actions' },
    ...(auditFilterOptions.actions || []).map((a) => ({ value: a, label: a })),
  ];
  const entityOptions = [
    { value: '', label: 'All entities' },
    ...(auditFilterOptions.entity_types || []).map((e) => ({ value: e, label: e })),
  ];
  const projectOptions = [
    { value: '', label: 'All projects' },
    ...(auditFilterOptions.projects || []).map((p) => ({ value: String(p.id), label: p.name })),
  ];
  const pageSizeOptions = (auditFilterOptions.page_sizes || [25, 50, 100]).map((n) => ({
    value: String(n), label: String(n),
  }));

  const USER_COLUMNS = [
    { key: 'username', header: 'Username', sortable: true },
    { key: 'role_display', header: 'Role', render: (_, row) => row.role_display || row.role },
    { key: 'is_active', header: 'Status', render: (v) => (v !== false ? '✅ Active' : '🚫 Inactive') },
    { key: 'created_at', header: 'Created', render: (v) => (v ? formatDate(v) : '—') },
  ];

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Analytics' }]} noPadding>
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
          {visibleMainTabs.map((tab) => (
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

              {isAdmin && projectRows.length > 0 && (
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
                <h3 className={styles.card__title}>🏆 Prompt Performance Leaderboard</h3>
                <p className={styles.card__caption}>Average quality rating per prompt template, computed from reviewer feedback.</p>
                {promptPerf.length > 0 ? (
                  <Table
                    columns={[
                      { key: 'prompt', header: 'Prompt', sortable: true },
                      { key: 'avg_rating', header: 'Avg Rating', align: 'right' },
                      { key: 'samples', header: 'Samples', align: 'right' },
                    ]}
                    rows={promptPerf}
                    rowKey="prompt"
                    pagination
                    pageSize={10}
                  />
                ) : (
                  <p className={styles.emptyHint}>No rated content yet. Score blocks in the Editor tab to see performance data here.</p>
                )}
              </section>

              <section className={styles.card}>
                <h3 className={styles.card__title}>🎯 Instructional Quality Trends</h3>
                {qualityChartData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={200}>
                    <AreaChart data={qualityChartData}>
                      <XAxis dataKey="index" tick={{ fontSize: 12 }} />
                      <YAxis domain={[1, 5]} tick={{ fontSize: 12 }} />
                      <Tooltip />
                      <Area type="monotone" dataKey="rating" stroke="#4f46e5" fill="#eef2ff" />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <p className={styles.emptyHint}>No rated blocks yet.</p>
                )}
              </section>

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

              <section className={styles.card}>
                <h3 className={styles.card__title}>🔍 System Event Log (Observability)</h3>
                <p className={styles.card__caption}>Full trace of system events — generations, uploads, exports, reviews, prompt changes.</p>
                <Table
                  columns={[
                    { key: 'event_type', header: 'Event' },
                    { key: 'actor', header: 'Actor' },
                    { key: 'details', header: 'Details' },
                    { key: 'created_at', header: 'Time', render: (v) => formatDateTime(v) },
                  ]}
                  rows={(systemLogs?.items || []).map((r, i) => ({ ...r, id: r.id ?? `log-${i}` }))}
                  rowKey="id"
                  pagination
                  pageSize={50}
                  serverSide
                  totalCount={systemLogs?.total ?? 0}
                  currentPage={systemLogPage}
                  onPageChange={handleSystemLogPage}
                  emptyTitle="No system events yet"
                  emptyMessage="Events will appear here as you use the platform."
                />
              </section>

              {canClearDb && (
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
              </>
            )}
          </>
        )}

        {/* ── Audit Trail ── */}
        {activeTab === 'Audit Trail' && canViewAudit && (
          <>
            <SectionBadge icon="🗒️" title="Audit Trail" subtitle="Structured log of every significant action — workflow transitions, approvals, exports, logins, role changes, and more." />
            <section className={styles.card}>
              <div className={styles.card__header}>
                <h3 className={styles.card__title}>Audit events</h3>
                {hasPermission('export.audit_log') && (
                  <Button variant="ghost" size="sm" onClick={() => dispatch(exportAuditThunk({ filters: auditFilters }))}>
                    ⬇️ Export to CSV
                  </Button>
                )}
              </div>

              <div className={styles.auditFilters}>
                <Select
                  label="User"
                  options={actorOptions}
                  value={auditFilters.actor}
                  onChange={(e) => updateAuditFilter({ actor: e.target.value })}
                  disabled={!isAdmin}
                />
                <Select
                  label="Action"
                  options={actionOptions}
                  value={auditFilters.action}
                  onChange={(e) => updateAuditFilter({ action: e.target.value })}
                />
                <Select
                  label="Entity type"
                  options={entityOptions}
                  value={auditFilters.entityType}
                  onChange={(e) => updateAuditFilter({ entityType: e.target.value })}
                />
                {isAdmin && (
                  <Select
                    label="Project"
                    options={projectOptions}
                    value={auditFilters.projectId}
                    onChange={(e) => updateAuditFilter({ projectId: e.target.value })}
                  />
                )}
                <Input
                  label="Date from"
                  type="date"
                  value={auditFilters.dateFrom}
                  onChange={(e) => updateAuditFilter({ dateFrom: e.target.value })}
                />
                <Input
                  label="Date to"
                  type="date"
                  value={auditFilters.dateTo}
                  onChange={(e) => updateAuditFilter({ dateTo: e.target.value })}
                />
                <Select
                  label="Rows per page"
                  options={pageSizeOptions}
                  value={String(auditFilters.pageSize)}
                  onChange={(e) => updateAuditFilter({ pageSize: Number(e.target.value) })}
                />
              </div>
              {!isAdmin && (
                <p className={styles.emptyHint}>Showing your events only.</p>
              )}

              {auditTrailError && (
                <p className={styles.emptyHint} role="alert">{auditTrailError}</p>
              )}

              {(auditTrail?.total ?? 0) > 0 && (
                <p className={styles.auditSummary}>
                  {auditTrail.total} event(s) total · showing page results
                  {auditTrail.pages > 0 && (
                    <> · Page {auditPage} of {auditTrail.pages}</>
                  )}
                </p>
              )}

              <Table
                columns={AUDIT_COLUMNS}
                rows={auditTrail?.items || []}
                rowKey="id"
                isLoading={auditTrailLoading}
                pagination
                pageSize={auditFilters.pageSize}
                serverSide
                totalCount={auditTrail?.total ?? 0}
                currentPage={auditPage}
                onPageChange={handleAuditPage}
                emptyTitle="No audit events match your filters"
                emptyMessage="Audit events appear here as users work in the platform."
              />
            </section>
          </>
        )}

        {/* ── User Management (Streamlit admin sections) ── */}
        {activeTab === 'User Management' && canManageUsers && (
          <>
            <section className={styles.card}>
              <div className={styles.card__header}>
                <h3 className={styles.card__title}>👥 User Management</h3>
                <Button variant="primary" size="sm" onClick={() => setShowUserModal(true)}>+ New User</Button>
              </div>
              <p className={styles.card__caption}>
                Admin-only view. Manage platform users, roles, and access control.
              </p>
              <Table columns={USER_COLUMNS} rows={users} rowKey="id" pagination pageSize={20} emptyTitle="No users" />
            </section>

            {users.length > 1 && toggleUserOptions.length > 0 && (
              <section className={styles.card}>
                <h4 className={styles.sectionHeading}>🔒 Toggle User Access</h4>
                <div className={styles.toggleAccess}>
                  <Select
                    label="Select user to toggle"
                    options={toggleUserOptions}
                    value={toggleUserSelect}
                    onChange={(e) => setToggleUserSelect(e.target.value)}
                    wrapperClassName={styles.toggleSelect}
                  />
                  {toggleActionLabel && (
                    <Button
                      variant={selectedToggleUser?.is_active !== false ? 'danger-ghost' : 'primary'}
                      size="sm"
                      className={styles.toggleAccess__btn}
                      onClick={handleToggleAccess}
                    >
                      {toggleActionLabel}
                    </Button>
                  )}
                </div>
              </section>
            )}
          </>
        )}

        <Modal
          open={showUserModal}
          onClose={() => setShowUserModal(false)}
          title="Create New User"
          size="sm"
          footer={(
            <>
              <Button variant="ghost" onClick={() => setShowUserModal(false)}>Cancel</Button>
              <Button variant="primary" onClick={userForm.handleSubmit(onCreateUser)}>Create</Button>
            </>
          )}
        >
          <form className={styles.form}>
            <Input label="Username" required error={userForm.formState.errors.username?.message} {...userForm.register('username')} />
            <Input label="Password" type="password" required error={userForm.formState.errors.password?.message} {...userForm.register('password')} />
            <Select
              label="Role"
              required
              options={[
                { value: 'author', label: 'ID' },
                { value: 'reviewer', label: 'Lead' },
                { value: 'admin', label: 'Admin' },
              ]}
              error={userForm.formState.errors.role?.message}
              {...userForm.register('role')}
            />
          </form>
        </Modal>
      </div>
    </PageContainer>
  );
}
