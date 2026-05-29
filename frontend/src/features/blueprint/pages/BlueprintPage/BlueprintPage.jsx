import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchBlueprintsThunk, generateBlueprintThunk, setActiveBlueprintThunk, fetchBlueprintVersionsThunk, commitBlueprintVersionThunk } from '@features/blueprint/blueprintThunks';
import { selectBlueprints, selectActiveBlueprint, selectBlueprintVersions, selectBlueprintLoading, selectBlueprintGenerating, setGenerationMode, selectBlueprintGenerationMode } from '@features/blueprint/blueprintSlice';
import { selectActiveCdd, selectCdds } from '@features/cdd/cddSlice';
import { fetchCddsThunk } from '@features/cdd/cddThunks';
import { createBlueprintSchema, commitVersionSchema } from '@utils/validation';
import { GENERATION_MODES } from '@utils/constants';
import { formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import Input from '@components/common/Input/Input';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import styles from './BlueprintPage.module.scss';

export default function BlueprintPage() {
  const { courseId }  = useParams();
  const dispatch      = useAppDispatch();
  const blueprints    = useAppSelector(selectBlueprints);
  const activeBlueprint = useAppSelector(selectActiveBlueprint);
  const versions      = useAppSelector(selectBlueprintVersions);
  const cdds          = useAppSelector(selectCdds);
  const activeCdd     = useAppSelector(selectActiveCdd);
  const genMode       = useAppSelector(selectBlueprintGenerationMode);
  const isLoading     = useAppSelector(selectBlueprintLoading);
  const isGenerating  = useAppSelector(selectBlueprintGenerating);

  const [showVersionModal, setShowVersionModal] = useState(false);

  const generateForm = useForm({ resolver: zodResolver(createBlueprintSchema) });
  const versionForm  = useForm({ resolver: zodResolver(commitVersionSchema) });

  useEffect(() => {
    dispatch(fetchBlueprintsThunk(courseId));
    dispatch(fetchCddsThunk(courseId));
  }, [courseId, dispatch]);

  useEffect(() => {
    if (activeCdd?.id) {
      generateForm.setValue('cdd_id', activeCdd.id);
    }
  }, [activeCdd, generateForm]);

  async function onGenerate(data) {
    dispatch(generateBlueprintThunk({
      ...data,
      course_id: Number(courseId),
      generation_mode: genMode,
    }));
  }

  async function onCommitVersion(data) {
    if (!activeBlueprint?.id) return;
    await dispatch(commitBlueprintVersionThunk({ blueprintId: activeBlueprint.id, data }));
    setShowVersionModal(false);
    versionForm.reset();
  }

  const cddOptions = cdds.map((c) => ({
    value: c.id,
    label: `${c.title || c.course_title}${activeCdd?.id === c.id ? ' ★ Active' : ''}`,
  }));

  // Derive module options from active CDD
  // TODO: parse module numbers from activeCdd.active_version.sections once backend contract confirmed
  const moduleOptions = Array.from({ length: 10 }, (_, i) => ({ value: i + 1, label: `Module ${i + 1}` }));

  return (
    <PageContainer
      title="Module Blueprint"
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Blueprint' }]}
      headerActions={
        activeBlueprint && (
          <Button variant="secondary" size="sm" onClick={() => setShowVersionModal(true)}>
            + Save Version
          </Button>
        )
      }
    >
      <div className={styles.layout}>
        {/* Left: Form */}
        <section className={styles.panel}>
          <h2 className={styles.panel__title}>Generate Blueprint</h2>

          {activeCdd ? (
            <div className={styles.cddBadge}>
              <span aria-hidden="true">📋</span> Linked CDD: <strong>{activeCdd.title || activeCdd.course_title}</strong>
            </div>
          ) : (
            <div className={styles.warning}>
              ⚠️ No active CDD. Go to the CDD tab first.
            </div>
          )}

          {/* Generation mode toggle */}
          <div className={styles.modeToggle} role="radiogroup" aria-label="Generation mode">
            {[GENERATION_MODES.STUDENT, GENERATION_MODES.TEACHER].map((mode) => (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={genMode === mode}
                className={`${styles.modeBtn} ${genMode === mode ? styles['modeBtn--active'] : ''}`}
                onClick={() => dispatch(setGenerationMode(mode))}
              >
                {mode === GENERATION_MODES.STUDENT ? '🎓 Student' : '👨‍🏫 Teacher'}
              </button>
            ))}
          </div>

          <form onSubmit={generateForm.handleSubmit(onGenerate)} className={styles.form}>
            <Select
              label="Linked CDD"
              required
              options={cddOptions}
              placeholder="Select a CDD"
              error={generateForm.formState.errors.cdd_id?.message}
              {...generateForm.register('cdd_id', { valueAsNumber: true })}
            />
            <Select
              label="Module"
              required
              options={moduleOptions}
              placeholder="Select module number"
              error={generateForm.formState.errors.module_number?.message}
              {...generateForm.register('module_number', { valueAsNumber: true })}
            />
            <Input label="Document Title (optional)" {...generateForm.register('document_title')} />
            <Button type="submit" variant="primary" fullWidth loading={isGenerating} disabled={!activeCdd}>
              {isGenerating ? 'Generating Blueprint…' : '✨ Generate Blueprint'}
            </Button>
          </form>
        </section>

        {/* Right: Library + Preview */}
        <section className={styles.panel}>
          <div className={styles.panel__header}>
            <h2 className={styles.panel__title}>Blueprint Library</h2>
          </div>

          {isLoading ? (
            <div className={styles.center}><Loader size="lg" /></div>
          ) : blueprints.length === 0 ? (
            <EmptyState title="No blueprints yet" message="Generate your first blueprint using the form." />
          ) : (
            <ul className={styles.list}>
              {blueprints.map((bp) => (
                <li
                  key={bp.id}
                  className={`${styles.listItem} ${activeBlueprint?.id === bp.id ? styles['listItem--active'] : ''}`}
                >
                  <div className={styles.listItem__info}>
                    <span className={styles.listItem__title}>{bp.title}</span>
                    <span className={styles.listItem__meta}>Module {bp.module_number} · {formatDate(bp.created_at)}</span>
                  </div>
                  {activeBlueprint?.id === bp.id ? (
                    <span className={styles.badge__active}>Active</span>
                  ) : (
                    <Button variant="ghost" size="sm" onClick={() => dispatch(setActiveBlueprintThunk({ blueprintId: bp.id, courseId: Number(courseId) }))}>
                      Set Active
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {activeBlueprint && (
            <div className={styles.activeContent}>
              <h3 className={styles.activeContent__title}>{activeBlueprint.title}</h3>
              {versions.length > 0 && (
                <div className={styles.versions}>
                  {versions.map((v) => (
                    <span key={v.id} className={`${styles.versionTag} ${v.is_active ? styles['versionTag--active'] : ''}`}>
                      {v.version}
                    </span>
                  ))}
                </div>
              )}
              {activeBlueprint.active_version?.full_content && (
                <div className={styles.contentPreview}>
                  <pre className={styles.contentPreview__text}>
                    {activeBlueprint.active_version.full_content}
                  </pre>
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      <Modal
        open={showVersionModal}
        onClose={() => setShowVersionModal(false)}
        title="Save New Version"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setShowVersionModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={versionForm.handleSubmit(onCommitVersion)}>Save</Button>
          </>
        }
      >
        <form className={styles.form}>
          <Input label="Version Tag" placeholder="v2" {...versionForm.register('tag')} />
          <Input label="Change Reason" required error={versionForm.formState.errors.reason?.message} {...versionForm.register('reason')} />
        </form>
      </Modal>
    </PageContainer>
  );
}
