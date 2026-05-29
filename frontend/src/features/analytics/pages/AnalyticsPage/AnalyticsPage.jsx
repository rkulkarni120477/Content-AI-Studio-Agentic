import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchSummaryThunk, fetchUsageThunk, fetchCostThunk,
  fetchAuditTrailThunk, fetchUsersThunk, createUserThunk,
  toggleUserActiveThunk, exportAuditThunk,
} from '@features/analytics/analyticsThunks';
import {
  selectSummary, selectUsage, selectCost, selectAuditTrail,
  selectUsers, selectAnalyticsLoading, setFilters, selectAnalyticsFilters,
} from '@features/analytics/analyticsSlice';
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
const DATE_RANGE_OPTIONS = Object.entries(DATE_RANGE_PRESETS).map(([, v]) => ({
  value: v, label: DATE_RANGE_LABELS[v],
}));

export default function AnalyticsPage() {
  const dispatch   = useAppDispatch();
  const { isAdmin } = useAuth();
  const summary    = useAppSelector(selectSummary);
  const usage      = useAppSelector(selectUsage);
  const cost       = useAppSelector(selectCost);
  const auditTrail = useAppSelector(selectAuditTrail);
  const users      = useAppSelector(selectUsers);
  const filters    = useAppSelector(selectAnalyticsFilters);
  const isLoading  = useAppSelector(selectAnalyticsLoading);

  const [activeTab, setActiveTab]     = useState(0);
  const [showUserModal, setShowUserModal] = useState(false);

  const userForm = useForm({ resolver: zodResolver(createUserSchema) });

  useEffect(() => {
    dispatch(fetchSummaryThunk(filters));
  }, [dispatch, filters.dateRange]);

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

  return (
    <PageContainer title="Analytics" breadcrumbs={[{ label: 'Analytics' }]}>
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
                  { label: 'Generations',  value: formatNumber(summary?.generations), icon: '⚡' },
                  { label: 'Blocks',       value: formatNumber(summary?.blocks),       icon: '📦' },
                  { label: 'Prompts',      value: formatNumber(summary?.prompt_assets),      icon: '📝' },
                  { label: 'CDDs',         value: formatNumber(summary?.cdds),         icon: '📋' },
                  { label: 'Blueprints',   value: formatNumber(summary?.blueprints),   icon: '🗺️'  },
                  { label: 'Total Cost',   value: formatCurrency(summary?.total_cost),       icon: '💰' },
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

              {/* Prompt performance table */}
              {summary?.prompt_performance && (
                <section className={styles.card}>
                  <h3 className={styles.card__title}>Prompt Performance</h3>
                  <Table
                    columns={[
                      { key: 'prompt_name', header: 'Prompt',   sortable: true },
                      { key: 'usage_count', header: 'Uses',     sortable: true, align: 'right' },
                      { key: 'avg_rating',  header: 'Avg Rating', sortable: true, align: 'right', render: (v) => v ? v.toFixed(2) : '—' },
                    ]}
                    rows={summary.prompt_performance}
                    rowKey="prompt_name"
                    pagination
                    pageSize={10}
                  />
                </section>
              )}
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
    </PageContainer>
  );
}
