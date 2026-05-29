import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { runGenerationThunk, queueGenerationThunk, pollJobThunk } from '@features/generate/generateThunks';
import { selectIsGenerating, selectActiveJobId, selectJobStatus, selectJobProgress, selectLatestBlocks, selectGenerateError, clearJob } from '@features/generate/generateSlice';
import { selectActiveBlueprint, selectBlueprintComponents } from '@features/blueprint/blueprintSlice';
import { selectActiveCdd } from '@features/cdd/cddSlice';
import { fetchBlueprintComponentsThunk } from '@features/blueprint/blueprintThunks';
import { selectWorkspaceConfig } from '@features/dashboard/dashboardSlice';
import { generateSchema } from '@utils/validation';
import { JOB_STATUSES } from '@utils/constants';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import Input from '@components/common/Input/Input';
import Loader from '@components/common/Loader/Loader';
import ErrorState from '@components/common/ErrorState/ErrorState';
import styles from './GeneratePage.module.scss';

export default function GeneratePage() {
  const { courseId } = useParams();
  const dispatch     = useAppDispatch();

  const isGenerating  = useAppSelector(selectIsGenerating);
  const activeJobId   = useAppSelector(selectActiveJobId);
  const jobStatus     = useAppSelector(selectJobStatus);
  const jobProgress   = useAppSelector(selectJobProgress);
  const latestBlocks  = useAppSelector(selectLatestBlocks);
  const generateError = useAppSelector(selectGenerateError);
  const activeCdd     = useAppSelector(selectActiveCdd);
  const activeBlueprint = useAppSelector(selectActiveBlueprint);
  const components    = useAppSelector(selectBlueprintComponents);
  const config        = useAppSelector(selectWorkspaceConfig);

  const { register, handleSubmit, formState: { errors } } = useForm({
    resolver: zodResolver(generateSchema),
    defaultValues: { block_count: 5 },
  });

  const isReady = Boolean(activeCdd && activeBlueprint);

  useEffect(() => {
    if (activeBlueprint?.id) dispatch(fetchBlueprintComponentsThunk(activeBlueprint.id));
  }, [activeBlueprint, dispatch]);

  // Start polling when a job is queued
  useEffect(() => {
    if (activeJobId && jobStatus === JOB_STATUSES.PENDING) {
      dispatch(pollJobThunk(activeJobId));
    }
  }, [activeJobId, jobStatus, dispatch]);

  async function onGenerate(data) {
    const payload = {
      ...data,
      course_id:    Number(courseId),
      cdd_id:       activeCdd?.id,
      blueprint_id: activeBlueprint?.id,
      model_choice: config.modelChoice,
      expert_domain:     config.expertDomain,
      target_audience:   config.targetAudience,
      audience_category: config.audienceCategory,
    };

    const asyncEnabled = import.meta.env.VITE_ENABLE_ASYNC_GENERATION === 'true';
    if (asyncEnabled) {
      dispatch(queueGenerationThunk(payload));
    } else {
      dispatch(runGenerationThunk(payload));
    }
  }

  const componentOptions = components.map((c) => ({ value: c.key, label: c.label }));

  return (
    <PageContainer
      title="Generate Content"
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Generate' }]}
    >
      {/* Workflow Steps */}
      <div className={styles.steps}>
        <div className={`${styles.step} ${activeCdd ? styles['step--done'] : styles['step--pending']}`}>
          <span className={styles.step__icon}>{activeCdd ? '✅' : '⬜'}</span>
          <div>
            <div className={styles.step__label}>CDD</div>
            <div className={styles.step__value}>{activeCdd ? activeCdd.title || activeCdd.course_title : 'Not set'}</div>
          </div>
        </div>
        <div className={styles.steps__connector} aria-hidden="true">→</div>
        <div className={`${styles.step} ${activeBlueprint ? styles['step--done'] : styles['step--pending']}`}>
          <span className={styles.step__icon}>{activeBlueprint ? '✅' : '⬜'}</span>
          <div>
            <div className={styles.step__label}>Blueprint</div>
            <div className={styles.step__value}>{activeBlueprint ? activeBlueprint.title : 'Not set'}</div>
          </div>
        </div>
        <div className={styles.steps__connector} aria-hidden="true">→</div>
        <div className={`${styles.step} ${isReady ? styles['step--ready'] : styles['step--pending']}`}>
          <span className={styles.step__icon}>{isReady ? '🚀' : '⬜'}</span>
          <div>
            <div className={styles.step__label}>Ready to Generate</div>
          </div>
        </div>
      </div>

      {!isReady && (
        <div className={styles.notReady}>
          ⚠️ Please set an active CDD and Blueprint before generating content.
        </div>
      )}

      <div className={styles.layout}>
        {/* Generate Form */}
        <section className={styles.panel}>
          <h2 className={styles.panel__title}>Generation Settings</h2>

          <form onSubmit={handleSubmit(onGenerate)} className={styles.form}>
            <Select
              label="Component"
              required
              options={componentOptions}
              placeholder={components.length === 0 ? 'Load blueprint to see components' : 'Select component'}
              error={errors.component_key?.message}
              disabled={!isReady || components.length === 0}
              {...register('component_key')}
            />
            <Input
              label="Number of Blocks"
              type="number"
              min={1}
              max={20}
              error={errors.block_count?.message}
              {...register('block_count', { valueAsNumber: true })}
            />
            <div className={styles.form__textarea}>
              <label className={styles.form__label}>Extra Instructions (optional)</label>
              <textarea
                className={styles.form__textareaInput}
                rows={4}
                placeholder="Any additional guidance for the AI…"
                {...register('extra_instructions')}
              />
            </div>
            <Button
              type="submit"
              variant="primary"
              fullWidth
              loading={isGenerating}
              disabled={!isReady}
            >
              {isGenerating ? 'Generating…' : '✨ Generate Content'}
            </Button>
          </form>

          {generateError && <ErrorState message={generateError} />}
        </section>

        {/* Job Progress / Results */}
        <section className={styles.panel}>
          <h2 className={styles.panel__title}>
            {activeJobId ? 'Generation Progress' : 'Latest Results'}
          </h2>

          {activeJobId && (
            <div className={styles.jobProgress}>
              <div className={styles.jobProgress__status}>
                {jobStatus === JOB_STATUSES.PENDING && <><Loader size="sm" /> Queued…</>}
                {jobStatus === JOB_STATUSES.RUNNING && <><Loader size="sm" /> Running…</>}
                {jobStatus === JOB_STATUSES.COMPLETED && '✅ Completed'}
                {jobStatus === JOB_STATUSES.FAILED && '❌ Failed'}
              </div>
              {jobProgress.map((msg, i) => (
                <div key={i} className={styles.jobProgress__stage}>{msg}</div>
              ))}
              {jobStatus !== JOB_STATUSES.COMPLETED && (
                <Button variant="ghost" size="sm" onClick={() => dispatch(clearJob())}>Cancel</Button>
              )}
            </div>
          )}

          {latestBlocks.length > 0 && (
            <div className={styles.results}>
              <p className={styles.results__count}>{latestBlocks.length} block(s) generated</p>
              {latestBlocks.map((block, i) => (
                <div key={block.id || i} className={styles.blockCard}>
                  <div className={styles.blockCard__header}>
                    <span className={styles.blockCard__label}>{block.block_label || `Block ${i + 1}`}</span>
                    <span className={styles.blockCard__type}>{block.block_type}</span>
                  </div>
                  <p className={styles.blockCard__preview}>
                    {(block.content || '').slice(0, 200)}{block.content?.length > 200 ? '…' : ''}
                  </p>
                </div>
              ))}
            </div>
          )}

          {!activeJobId && latestBlocks.length === 0 && (
            <div className={styles.empty}>
              <span aria-hidden="true">✨</span>
              <p>Generated blocks will appear here.</p>
            </div>
          )}
        </section>
      </div>
    </PageContainer>
  );
}
