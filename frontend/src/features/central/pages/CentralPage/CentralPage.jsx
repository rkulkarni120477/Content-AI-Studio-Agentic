import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchCentralItemsThunk, createCentralItemThunk,
  importFromRegistryThunk, archiveCentralItemThunk,
} from '@features/central/centralThunks';
import {
  selectCentralItems, selectCentralTotal, selectCentralLoading, selectCentralError,
} from '@features/central/centralSlice';
import { createCentralItemSchema, importFromRegistrySchema } from '@utils/validation';
import { useAuth } from '@hooks/useAuth';
import { ROLES } from '@utils/constants';
import { formatDate } from '@utils/helpers';
import { useNavigate } from 'react-router-dom';
import SelectionLayout from '@components/layout/SelectionLayout/SelectionLayout';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import Button from '@components/common/Button/Button';
import { ROUTES } from '@utils/constants';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import Table from '@components/common/Table/Table';
import Modal from '@components/common/Modal/Modal';
import SearchBar from '@components/common/SearchBar/SearchBar';
import ErrorState from '@components/common/ErrorState/ErrorState';
import toast from 'react-hot-toast';
import styles from './CentralPage.module.scss';

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'archived', label: 'Archived' },
];

export default function CentralPage() {
  const dispatch  = useAppDispatch();
  const navigate  = useNavigate();
  const { role }  = useAuth();
  const items     = useAppSelector(selectCentralItems);
  const total     = useAppSelector(selectCentralTotal);
  const isLoading = useAppSelector(selectCentralLoading);
  const error     = useAppSelector(selectCentralError);

  const [activeForm, setActiveForm] = useState('create');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [viewItem, setViewItem] = useState(null);
  const [reuseContent, setReuseContent] = useState('');

  const createForm = useForm({ resolver: zodResolver(createCentralItemSchema) });
  const importForm = useForm({ resolver: zodResolver(importFromRegistrySchema) });

  function loadItems() {
    dispatch(fetchCentralItemsThunk({
      search: search.trim() || undefined,
      status: statusFilter || undefined,
    }));
  }

  useEffect(() => {
    loadItems();
  }, [dispatch, search, statusFilter]);

  const metrics = useMemo(() => {
    const active = items.filter((i) => i.status === 'active').length;
    const archived = items.filter((i) => i.status === 'archived').length;
    const prompts = items.filter((i) => (i.item_type || i.tags || '').toLowerCase().includes('prompt')).length;
    return { active, archived, prompts, total: total || items.length };
  }, [items, total]);

  if (role !== ROLES.ADMIN) {
    return (
      <SelectionLayout sidebarProps={{ variant: 'project' }}>
        <ErrorState title="Access Denied" message="Only Admins can access the Central Repository." />
      </SelectionLayout>
    );
  }

  async function onCreateItem(data) {
    const result = await dispatch(createCentralItemThunk(data));
    if (!result.error) createForm.reset();
  }

  async function onImport(data) {
    const result = await dispatch(importFromRegistryThunk(data));
    if (result.error) toast.error(result.payload || 'Import failed.');
    else importForm.reset();
  }

  async function onArchive(item) {
    if (!window.confirm(`Archive "${item.name}"?`)) return;
    await dispatch(archiveCentralItemThunk(item.id));
  }

  function onReuse(item) {
    const stub = [
      `# ${item.name}`,
      item.tags ? `Tags: ${item.tags}` : '',
      item.status ? `Status: ${item.status}` : '',
      '',
      '(Full item content is not returned by the list API — reuse metadata or re-import from Prompt Registry.)',
    ].filter(Boolean).join('\n');
    setReuseContent(stub);
    navigator.clipboard?.writeText(stub).then(() => {
      toast.success('Metadata copied to clipboard.');
    }).catch(() => {
      toast.success('Reuse panel opened — copy manually.');
    });
  }

  const COLUMNS = [
    { key: 'name', header: 'Name', sortable: true, render: (_, row) => row.name || row.title },
    { key: 'item_type', header: 'Type', render: (_, row) => row.item_type || '—' },
    { key: 'tags', header: 'Tags', render: (v) => v || '—' },
    {
      key: 'status',
      header: 'Status',
      render: (v) => (
        <span className={v === 'active' ? styles.statusActive : styles.statusArchived}>
          {(v || 'active').charAt(0).toUpperCase() + (v || 'active').slice(1)}
        </span>
      ),
    },
    { key: 'created_at', header: 'Created', render: (v) => formatDate(v) },
    {
      key: 'actions',
      header: 'Actions',
      render: (_, row) => (
        <div className={styles.rowActions}>
          <Button variant="ghost" size="sm" onClick={() => setViewItem(row)}>View</Button>
          <Button variant="ghost" size="sm" onClick={() => onReuse(row)}>Reuse</Button>
          {row.status !== 'archived' && (
            <Button variant="ghost" size="sm" onClick={() => onArchive(row)}>Archive</Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <SelectionLayout sidebarProps={{ variant: 'project' }}>
      <SelectionPageHeader title="Central Repository" subtitle="Admin-managed prompts, assets, and learnings." />
      <Button variant="ghost" size="sm" onClick={() => navigate(ROUTES.DASHBOARD)} style={{ marginBottom: '1rem' }}>
        ← Back to Projects
      </Button>

      <div className={styles.kpiBar}>
        <div className={styles.kpiCard}>
          <span className={styles.kpiCard__value}>{metrics.total}</span>
          <span className={styles.kpiCard__label}>Total Items</span>
        </div>
        <div className={styles.kpiCard}>
          <span className={styles.kpiCard__value}>{metrics.active}</span>
          <span className={styles.kpiCard__label}>Active</span>
        </div>
        <div className={styles.kpiCard}>
          <span className={styles.kpiCard__value}>{metrics.archived}</span>
          <span className={styles.kpiCard__label}>Archived</span>
        </div>
        <div className={styles.kpiCard}>
          <span className={styles.kpiCard__value}>{metrics.prompts}</span>
          <span className={styles.kpiCard__label}>Prompts</span>
        </div>
      </div>

      <div className={styles.layout}>
        <section className={styles.panel}>
          <div className={styles.formToggle} role="tablist">
            {['create', 'import'].map((f) => (
              <button
                key={f}
                role="tab"
                aria-selected={activeForm === f}
                className={`${styles.formToggleBtn} ${activeForm === f ? styles['formToggleBtn--active'] : ''}`}
                onClick={() => setActiveForm(f)}
                type="button"
              >
                {f === 'create' ? '✏️ Create Item' : '📥 Import from Registry'}
              </button>
            ))}
          </div>

          {activeForm === 'create' ? (
            <form onSubmit={createForm.handleSubmit(onCreateItem)} className={styles.form}>
              <Input label="Name" required error={createForm.formState.errors.name?.message} {...createForm.register('name')} />
              <Input label="Item Type" required placeholder="e.g., prompt, rubric, guideline" error={createForm.formState.errors.item_type?.message} {...createForm.register('item_type')} />
              <Input label="Description" {...createForm.register('description')} />
              <div className={styles.form__field}>
                <label className={styles.form__label}>Content <span className={styles.required}>*</span></label>
                <textarea className={`${styles.form__textarea} ${createForm.formState.errors.content ? styles['form__textarea--error'] : ''}`} rows={8} {...createForm.register('content')} />
                {createForm.formState.errors.content && <p className={styles.fieldError}>{createForm.formState.errors.content.message}</p>}
              </div>
              <Input label="Tags (comma-separated)" {...createForm.register('tags')} />
              <Button type="submit" variant="primary" fullWidth loading={isLoading}>Add to Repository</Button>
            </form>
          ) : (
            <form onSubmit={importForm.handleSubmit(onImport)} className={styles.form}>
              <Input
                label="Prompt Asset Name"
                required
                placeholder="e.g., lesson_generator"
                error={importForm.formState.errors.prompt_name?.message}
                {...importForm.register('prompt_name')}
              />
              <Input label="Specific Version (optional)" type="number" {...importForm.register('version', { valueAsNumber: true })} />
              <p className={styles.importHint}>
                Imports via Prompt Registry API, then creates a Central Repository item.
              </p>
              <Button type="submit" variant="primary" fullWidth loading={isLoading}>Import Prompt</Button>
            </form>
          )}
        </section>

        <section className={styles.panel}>
          <h2 className={styles.panel__title}>Repository Items</h2>
          <div className={styles.filters}>
            <SearchBar value={search} onChange={setSearch} placeholder="Search by keyword…" />
            <Select
              label="Status"
              options={STATUS_OPTIONS}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            />
          </div>
          {error ? (
            <ErrorState message={error} onRetry={loadItems} />
          ) : (
            <Table
              columns={COLUMNS}
              rows={items}
              rowKey="id"
              isLoading={isLoading}
              pagination
              pageSize={20}
              emptyTitle="No items yet"
              emptyMessage="Create or import your first repository item."
            />
          )}
        </section>
      </div>

      <Modal
        open={Boolean(viewItem)}
        onClose={() => setViewItem(null)}
        title={viewItem ? `View: ${viewItem.name}` : 'View Item'}
        size="md"
      >
        {viewItem && (
          <div className={styles.viewModal}>
            <p><strong>Type:</strong> {viewItem.item_type || '—'}</p>
            <p><strong>Status:</strong> {viewItem.status || 'active'}</p>
            <p><strong>Tags:</strong> {viewItem.tags || '—'}</p>
            <p><strong>Created:</strong> {formatDate(viewItem.created_at)}</p>
            <p className={styles.viewModal__note}>
              Full content is not included in the list API response. Use Import from Registry or create a new item with content.
            </p>
          </div>
        )}
      </Modal>

      {reuseContent && (
        <Modal open onClose={() => setReuseContent('')} title="Reuse — copied metadata" size="sm">
          <textarea className={styles.form__textarea} rows={6} readOnly value={reuseContent} />
        </Modal>
      )}
    </SelectionLayout>
  );
}
