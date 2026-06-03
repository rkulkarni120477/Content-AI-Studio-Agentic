import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchSummaryThunk, fetchUsageThunk, fetchCostThunk,
  fetchAuditTrailThunk, fetchUsersThunk, createUserThunk,
  toggleUserActiveThunk, exportAuditThunk,
  fetchProjectAnalyticsThunk, fetchPromptPerfThunk,
  fetchQualityTrendsThunk, fetchGenerationHistoryThunk,
} from '@features/analytics/analyticsThunks';
import {
  selectSummary, selectUsage, selectCost, selectAuditTrail,
  selectUsers, selectAnalyticsLoading, setFilters, selectAnalyticsFilters,
  selectProjectRows, selectPromptPerf, selectQualityRatings, selectGenHistory,
} from '@features/analytics/analyticsSlice';
import {
  selectSelectedProject, selectSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import { createUserSchema } from '@utils/validation';
import { DATE_RANGE_LABELS, DATE_RANGE_PRESETS } from '@utils/constants';
import { formatCurrency, formatDate, formatNumber, formatTokens } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import Input from '@components/common/Input/Input';
import Table from '@components/common/Table/Table';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import styles from './AnalyticsPage.module.scss';

const TABS = ['Overview', 'Cost & Usage', 'Audit Trail', 'User Management'];
const HISTORY_TABS = ['Generations', 'Registry Commits', 'Document Uploads', 'CDD & Blueprint Log'];
const DATE_RANGE_OPTIONS = Object.entries(DATE_RANGE_PRESETS).map(([, v]) => ({
  value: v, label: DATE_RANGE_LABELS[v],
}));

export default function AnalyticsPage() {
  const dispatch   = useAppDispatch();
  const { isAdmin } = useAuth();
  const summary    = useAppSelector(selectSummary);
  const projectRows = useAppSelector(selectProjectRows);
  const promptPerf = useAppSelector(selectPromptPerf);
  const qualityRatings = useAppSelector(selectQualityRatings);
  const genHistory = useAppSelector(selectGenHistory);
  const usage      = useAppSelector(selectUsage);
  const cost       = useAppSelector(selectCost);
  const auditTrail = useAppSelector(selectAuditTrail);
  const users      = useAppSelector(selectUsers);
  const filters    = useAppSelector(selectAnalyticsFilters);
  const isLoading  = useAppSelector(selectAnalyticsLoading);
  const selProject = useAppSelector(selectSelectedProject);
  const selCourse = useAppSelector(selectSelectedCourse);

  const [activeTab, setActiveTab]     = useState(0);
  const [historyTab, setHistoryTab] = useState(0);
  const [showUserModal, setShowUserModal] = useState(false);

  const userForm = useForm({ resolver: zodResolver(createUserSchema) });

  useEffect(() => {
    dispatch(fetchSummaryThunk(filters));
    dispatch(fetchPromptPerfThunk(filters));
    dispatch(fetchQualityTrendsThunk(filters));
    dispatch(fetchGenerationHistoryThunk(filters));
    if (isAdmin) dispatch(fetchProjectAnalyticsThunk());
  }, [dispatch, filters.dateRange, isAdmin]);

  useEffect(() => {
    if (activeTab === 1) dispatch(fetchUsageThunk(filters));
    if (activeTab === 1) dispatch(fetchCostThunk(filters));
    if (activeTab === 2) dispatch(fetchAuditTrailThunk(filters));
    if (activeTab === 3) dispatch(fetchUsersThunk());
  }, [dispatch, activeTab, filters]);

  async function onCreateUser(data) {
    const result = await dispatch(createUserThunk(data));
    if (!result.error) { setShowUserModal(false); userForm.reset(); }
  }

  const AUDIT_COLUMNS = [
    { key: 'actor',      header: 'User',    sortable: true },
    { key: 'action',     header: 'Action',  sortable: true },
    { key: 'entity_type',header: 'Entity' },
    { key: 'details',    header: 'Details', render: (v) => typeof v === 'object' ? JSON.stringify(v).slice(0, 60) : v },
    { key: 'created_at', header: 'Time',    render: (v) => formatDate(v) },
  ];

  const USER_COLUMNS = [
    { key: 'username',   header: 'Username',  sortable: true },
    { key: 'role',       header: 'Role' },
    { key: 'email',      header: 'Email',     render: (v) => v || '—' },
    { key: 'is_active',  header: 'Status',    render: (v) => v ? <span className={styles.badge__active}>Active</span> : <span className={styles.badge__inactive}>Inactive</span> },
    { key: 'created_at', header: 'Created',   render: (v) => formatDate(v) },
    {
      key: '__actions',
      header: '',
      render: (_, row) => (
        <Button variant="ghost" size="xs" onClick={() => dispatch(toggleUserActiveThunk(row.id))}>
          {row.is_active ? 'Deactivate' : 'Activate'}
        </Button>
      ),
    },
  ];

  const qualityChartData = (qualityRatings || []).map((r, i) => ({ index: i + 1, rating: r }));

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Analytics' }]} noPadding>
      <div className={styles.pageWrap}>
        <SectionBadge
          icon="📊"
          title="Analytics & Observability"
          subtitle="Real-time metrics, prompt performance tracking, and full audit trails for every action on the platform."
        />
        {!isAdmin && selProject && (
          <p className={styles.scopeCaption}>
            Showing your metrics for project <strong>{selProject.name}</strong>
            {selCourse ? <> → course <strong>{selCourse.name}</strong></> : null}.
          </p>
        )}
        {isAdmin && <p className={styles.scopeCaption}>Admin view — cross-project metrics.</p>}

      {/* Tab Bar */}
      <div className={styles.tabs} role="tablist">
        {TABS.map((tab, i) => (
          <button key={i} role="tab" aria-selected={activeTab === i}
            className={`${styles.tab} ${activeTab === i ? styles['tab--active'] : ''}`}
            onClick={() => setActiveTab(i)} type="button">
            {tab}
          </button>
        ))}
      </div>

      {/* Date Range Filter (global) */}
      <div className={styles.globalFilter}>
        <Select
          options={DATE_RANGE_OPTIONS}
          value={filters.dateRange}
          onChange={(e) => dispatch(setFilters({ dateRange: e.target.value }))}
          wrapperClassName={styles.dateRangeSelect}
        />
        <Button variant="ghost" size="sm" onClick={() => dispatch(fetchSummaryThunk(filters))}>
          Refresh
        </Button>
      </div>

      {/* Overview Tab */}
      {activeTab === 0 && (
        <>
          {isLoading ? (
            <div className={styles.center}><Loader size="xl" /></div>
          ) : (
            <>
              {/* KPI Cards */}
              <div className={styles.kpiGrid}>
                {[
                  { label: 'Generations',  value: formatNumber(summary?.generations), icon: '🚀' },
                  { label: 'Content Blocks', value: formatNumber(summary?.blocks), icon: '🧩' },
                  { label: 'Prompt Assets', value: formatNumber(summary?.prompt_assets), icon: '📚' },
                  { label: 'Documents', value: formatNumber(summary?.documents), icon: '📄' },
                  { label: 'CDDs', value: formatNumber(summary?.cdds), icon: '📋' },
                  { label: 'Blueprints', value: formatNumber(summary?.blueprints), icon: '🗂️' },
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
                      { key: 'blueprints', header: 'Blueprints', align: 'right' },
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
                    <button
                      key={t}
                      type="button"
                      className={`${styles.subTab} ${historyTab === i ? styles['subTab--active'] : ''}`}
                      onClick={() => setHistoryTab(i)}
                    >
                      {t}
                    </button>
                  ))}
                </div>
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
                  ) : (
                    <p className={styles.emptyHint}>No generations yet.</p>
                  )
                )}
                {historyTab > 0 && (
                  <p className={styles.emptyHint}>Detailed history for this tab is available in the Audit Trail tab.</p>
                )}
              </section>
            </>
          )}
        </>
      )}

      {/* Cost & Usage Tab */}
      {activeTab === 1 && (
        <div className={styles.chartsGrid}>
          {usage?.monthly && (
            <section className={styles.card}>
              <h3 className={styles.card__title}>Monthly Tokens</h3>
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={usage.monthly}>
                  <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} tickFormatter={formatTokens} />
                  <Tooltip formatter={(v) => formatTokens(v)} />
                  <Area type="monotone" dataKey="total_tokens" stroke="#4f46e5" fill="#eef2ff" />
                </AreaChart>
              </ResponsiveContainer>
            </section>
          )}

          {cost?.by_model && (
            <section className={styles.card}>
              <h3 className={styles.card__title}>Cost by Model</h3>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={cost.by_model}>
                  <XAxis dataKey="model_name" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => `$${v.toFixed(3)}`} />
                  <Tooltip formatter={(v) => formatCurrency(v)} />
                  <Bar dataKey="estimated_cost" fill="#4f46e5" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </section>
          )}
        </div>
      )}

      {/* Audit Trail Tab */}
      {activeTab === 2 && (
        <section className={styles.card}>
          <div className={styles.card__header}>
            <h3 className={styles.card__title}>Audit Trail</h3>
            <Button variant="ghost" size="sm" onClick={() => dispatch(exportAuditThunk(filters))}>
              ↓ Export CSV
            </Button>
          </div>
          <Table
            columns={AUDIT_COLUMNS}
            rows={auditTrail.items || []}
            rowKey="id"
            isLoading={isLoading}
            pagination
            pageSize={20}
            emptyTitle="No audit events"
          />
        </section>
      )}

      {/* User Management Tab */}
      {activeTab === 3 && isAdmin && (
        <section className={styles.card}>
          <div className={styles.card__header}>
            <h3 className={styles.card__title}>User Management</h3>
            <Button variant="primary" size="sm" onClick={() => setShowUserModal(true)}>+ New User</Button>
          </div>
          <Table
            columns={USER_COLUMNS}
            rows={users}
            rowKey="id"
            isLoading={isLoading}
            pagination
            pageSize={20}
            emptyTitle="No users"
          />
        </section>
      )}

      {/* Create User Modal */}
      <Modal
        open={showUserModal}
        onClose={() => setShowUserModal(false)}
        title="Create New User"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setShowUserModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={userForm.handleSubmit(onCreateUser)}>Create</Button>
          </>
        }
      >
        <form className={styles.form}>
          <Input label="Username" required error={userForm.formState.errors.username?.message} {...userForm.register('username')} />
          <Input label="Password" type="password" required error={userForm.formState.errors.password?.message} {...userForm.register('password')} />
          <Select label="Role" required options={[
            { value: 'admin', label: 'Admin' },
            { value: 'author', label: 'ID (Author)' },
            { value: 'reviewer', label: 'Lead (Reviewer)' },
          ]} error={userForm.formState.errors.role?.message} {...userForm.register('role')} />
          <Input label="Email (optional)" type="email" {...userForm.register('email')} />
        </form>
      </Modal>
      </div>
    </PageContainer>
  );
}
