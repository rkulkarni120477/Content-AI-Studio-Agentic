import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchCddsThunk, generateCddThunk, setActiveCddThunk, fetchCddVersionsThunk, commitCddVersionThunk, exportCddThunk } from '@features/cdd/cddThunks';
import { selectCdds, selectActiveCdd, selectCddVersions, selectCddLoading, selectCddGenerating, selectCddError } from '@features/cdd/cddSlice';
import { selectStyles, selectActiveStyle } from '@features/style/styleSlice';
import { fetchStylesThunk } from '@features/style/styleThunks';
import { createCddSchema, commitVersionSchema } from '@utils/validation';
import { EXPORT_FORMATS } from '@utils/constants';
import { downloadBlob, formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ErrorState from '@components/common/ErrorState/ErrorState';
import styles from './CddPage.module.scss';

export default function CddPage() {
  const { courseId } = useParams();
  const dispatch   = useAppDispatch();
  const cdds       = useAppSelector(selectCdds);
  const activeCdd  = useAppSelector(selectActiveCdd);
  const versions   = useAppSelector(selectCddVersions);
  const styles_list = useAppSelector(selectStyles);
  const activeStyle = useAppSelector(selectActiveStyle);
  const isLoading  = useAppSelector(selectCddLoading);
  const isGenerating = useAppSelector(selectCddGenerating);
  const error      = useAppSelector(selectCddError);

  const [showVersionModal, setShowVersionModal]   = useState(false);
  const [selectedCddId, setSelectedCddId]         = useState(null);

  const generateForm = useForm({ resolver: zodResolver(createCddSchema) });
  const versionForm  = useForm({ resolver: zodResolver(commitVersionSchema) });

  useEffect(() => {
    dispatch(fetchCddsThunk(courseId));
    dispatch(fetchStylesThunk());
  }, [courseId, dispatch]);

  useEffect(() => {
    if (activeCdd?.id) {
      setSelectedCddId(activeCdd.id);
      dispatch(fetchCddVersionsThunk(activeCdd.id));
    }
  }, [activeCdd, dispatch]);

  async function onGenerate(data) {
    const payload = { ...data, course_id: Number(courseId), style_id: activeStyle?.id || null };
    dispatch(generateCddThunk(payload));
  }

  async function onSetActive(cddId) {
    dispatch(setActiveCddThunk({ cddId, courseId: Number(courseId) }));
  }

  async function onCommitVersion(data) {
    if (!selectedCddId) return;
    await dispatch(commitCddVersionThunk({ cddId: selectedCddId, data }));
    setShowVersionModal(false);
    versionForm.reset();
  }

  async function onExport(format) {
    const response = await dispatch(exportCddThunk({ cddId: activeCdd.id, format })).unwrap();
    downloadBlob(response.data, `cdd-${activeCdd.id}.${format}`);
  }

  const styleOptions = styles_list.map((s) => ({ value: s.id, label: `${s.name}${s.is_active ? ' ★' : ''}` }));

  return (
    <PageContainer
      title="Course Design Document"
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'CDD' }]}
      headerActions={
        activeCdd && (
          <div className={styles.headerActions}>
            <Button variant="ghost" size="sm" onClick={() => onExport('markdown')}>↓ MD</Button>
            <Button variant="ghost" size="sm" onClick={() => onExport('docx')}>↓ DOCX</Button>
          </div>
        )
      }
    >
      <div className={styles.layout}>
        {/* Left: Generate Form */}
        <section className={styles.panel}>
          <h2 className={styles.panel__title}>Generate New CDD</h2>

          {activeStyle && (
            <div className={styles.styleIndicator}>
              <span aria-hidden="true">🎨</span> Active Style: <strong>{activeStyle.name}</strong>
            </div>
          )}

          <form onSubmit={generateForm.handleSubmit(onGenerate)} className={styles.form}>
            <Input
              label="Course Title"
              required
              placeholder="e.g., Patient Assessment for Nurses"
              error={generateForm.formState.errors.course_title?.message}
              {...generateForm.register('course_title')}
            />
            <Input
              label="Document Title (optional)"
              placeholder="e.g., CDD v1"
              {...generateForm.register('document_title')}
            />
            <Input
              label="Estimated Duration (hours)"
              type="number"
              min={1}
              placeholder="e.g., 8"
              {...generateForm.register('duration_hours', { valueAsNumber: true })}
            />
            <Select
              label="Style"
              options={styleOptions}
              placeholder="— No style (use defaults) —"
              {...generateForm.register('style_id', { valueAsNumber: true })}
            />
            <Button type="submit" variant="primary" fullWidth loading={isGenerating}>
              {isGenerating ? 'Generating CDD…' : '✨ Generate CDD'}
            </Button>
          </form>

          {error && <ErrorState message={error} onRetry={() => dispatch(fetchCddsThunk(courseId))} />}
        </section>

        {/* Right: CDD Library */}
        <section className={styles.panel}>
          <div className={styles.panel__header}>
            <h2 className={styles.panel__title}>CDD Library</h2>
          </div>

          {isLoading ? (
            <div className={styles.center}><Loader size="lg" /></div>
          ) : cdds.length === 0 ? (
            <EmptyState title="No CDDs yet" message="Generate your first CDD using the form." />
          ) : (
            <ul className={styles.list}>
              {cdds.map((cdd) => (
                <li
                  key={cdd.id}
                  className={`${styles.listItem} ${activeCdd?.id === cdd.id ? styles['listItem--active'] : ''}`}
                >
                  <div className={styles.listItem__info}>
                    <span className={styles.listItem__title}>{cdd.title || cdd.course_title}</span>
                    <span className={styles.listItem__meta}>{formatDate(cdd.created_at)}</span>
                  </div>
                  {activeCdd?.id === cdd.id ? (
                    <span className={styles.badge__active}>Active</span>
                  ) : (
                    <Button variant="ghost" size="sm" onClick={() => onSetActive(cdd.id)}>
                      Set Active
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {/* Active CDD Content */}
          {activeCdd && (
            <div className={styles.activeContent}>
              <div className={styles.activeContent__header}>
                <h3 className={styles.activeContent__title}>{activeCdd.title || activeCdd.course_title}</h3>
                <Button variant="secondary" size="sm" onClick={() => setShowVersionModal(true)}>
                  + Save Version
                </Button>
              </div>

              {/* Version selector */}
              {versions.length > 0 && (
                <div className={styles.versions}>
                  <span className={styles.versions__label}>Versions:</span>
                  {versions.map((v) => (
                    <span key={v.id} className={`${styles.versionTag} ${v.is_active ? styles['versionTag--active'] : ''}`}>
                      {v.version}
                    </span>
                  ))}
                </div>
              )}

              {/* CDD Content Preview */}
              {activeCdd.active_version?.full_content && (
                <div className={`${styles.contentPreview} markdown-content`}>
                  <pre className={styles.contentPreview__text}>
                    {activeCdd.active_version.full_content}
                  </pre>
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {/* Commit Version Modal */}
      <Modal
        open={showVersionModal}
        onClose={() => setShowVersionModal(false)}
        title="Save New Version"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setShowVersionModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={versionForm.handleSubmit(onCommitVersion)}>Save Version</Button>
          </>
        }
      >
        <form className={styles.form}>
          <Input label="Version Tag (optional)" placeholder="e.g., v2, final" {...versionForm.register('tag')} />
          <Input
            label="Change Reason"
            required
            placeholder="e.g., Updated learning objectives"
            error={versionForm.formState.errors.reason?.message}
            {...versionForm.register('reason')}
          />
        </form>
      </Modal>
    </PageContainer>
  );
}
