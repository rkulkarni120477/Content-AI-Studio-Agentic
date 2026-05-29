import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchCentralItemsThunk, createCentralItemThunk, importFromRegistryThunk } from '@features/central/centralThunks';
import { selectCentralItems, selectCentralTotal, selectCentralLoading, selectCentralError } from '@features/central/centralSlice';
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
import ErrorState from '@components/common/ErrorState/ErrorState';
import styles from './CentralPage.module.scss';

export default function CentralPage() {
  const dispatch  = useAppDispatch();
  const navigate  = useNavigate();
  const { role }  = useAuth();
  const items     = useAppSelector(selectCentralItems);
  const total     = useAppSelector(selectCentralTotal);
  const isLoading = useAppSelector(selectCentralLoading);
  const error     = useAppSelector(selectCentralError);

  const [activeForm, setActiveForm] = useState('create');

  const createForm = useForm({ resolver: zodResolver(createCentralItemSchema) });
  const importForm = useForm({ resolver: zodResolver(importFromRegistrySchema) });

  useEffect(() => {
    dispatch(fetchCentralItemsThunk());
  }, [dispatch]);

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
    if (!result.error) importForm.reset();
  }

  const COLUMNS = [
    { key: 'name',        header: 'Name',      sortable: true },
    { key: 'item_type',   header: 'Type' },
    { key: 'description', header: 'Description', render: (v) => v || '—' },
    { key: 'tags',        header: 'Tags',       render: (v) => v || '—' },
    { key: 'created_at',  header: 'Created',   render: (v) => formatDate(v) },
  ];

  return (
    <SelectionLayout sidebarProps={{ variant: 'project' }}>
      <SelectionPageHeader title="Central Repository" subtitle="Admin-managed prompts, assets, and learnings." />
      <Button variant="ghost" size="sm" onClick={() => navigate(ROUTES.DASHBOARD)} style={{ marginBottom: '1rem' }}>
        ← Back to Projects
      </Button>
      <div className={styles.kpiBar}>
        <div className={styles.kpiCard}>
          <span className={styles.kpiCard__value}>{total}</span>
          <span className={styles.kpiCard__label}>Total Items</span>
        </div>
      </div>

      <div className={styles.layout}>
        {/* Form Panel */}
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
              <Button type="submit" variant="primary" fullWidth loading={isLoading}>Import Prompt</Button>
            </form>
          )}
        </section>

        {/* Items Table */}
        <section className={styles.panel}>
          <h2 className={styles.panel__title}>Repository Items</h2>
          {error ? (
            <ErrorState message={error} onRetry={() => dispatch(fetchCentralItemsThunk())} />
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
    </SelectionLayout>
  );
}
