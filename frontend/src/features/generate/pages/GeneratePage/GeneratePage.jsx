import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { launchGenerationThunk, pollJobThunk } from '@features/generate/generateThunks';
import {
  selectIsGenerating, selectActiveJobId, selectJobStatus,
  selectJobProgress, selectLatestBlocks, selectGenerateError,
  clearJob,
} from '@features/generate/generateSlice';
import { selectActiveBlueprint, selectBlueprintComponents } from '@features/blueprint/blueprintSlice';
import { selectActiveCdd } from '@features/cdd/cddSlice';
import { fetchBlueprintComponentsThunk } from '@features/blueprint/blueprintThunks';
import { fetchBlueprintsThunk } from '@features/blueprint/blueprintThunks';
import { fetchCddsThunk } from '@features/cdd/cddThunks';
import { fetchStylesThunk } from '@features/style/styleThunks';
import { selectActiveStyle } from '@features/style/styleSlice';
import { fetchPromptsThunk } from '@features/prompts/promptsThunks';
import { selectPrompts } from '@features/prompts/promptsSlice';
import {
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  selectModelChoice, selectExpertDomain, selectTargetAudience, selectAudienceCategory,
} from '@features/dashboard/dashboardSlice';
import { adminService } from '@features/admin/services/adminService';
import { generateService } from '@features/generate/services/generateService';
import { cddService } from '@features/cdd/services/cddService';
import { blueprintService } from '@features/blueprint/services/blueprintService';
import { buildPromptDownloadMd } from '@utils/promptDefaults';
import { JOB_STATUSES } from '@utils/constants';
import { downloadBlob } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import InlinePromptControls from '@components/generation/InlinePromptControls/InlinePromptControls';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import Loader from '@components/common/Loader/Loader';
import ErrorState from '@components/common/ErrorState/ErrorState';
import styles from './GeneratePage.module.scss';

export default function GeneratePage() {
  const { courseId } = useParams();
  const dispatch = useAppDispatch();

  const isGenerating = useAppSelector(selectIsGenerating);
  const activeJobId = useAppSelector(selectActiveJobId);
  const jobStatus = useAppSelector(selectJobStatus);
  const jobProgress = useAppSelector(selectJobProgress);
  const latestBlocks = useAppSelector(selectLatestBlocks);
  const generateError = useAppSelector(selectGenerateError);
  const activeCdd = useAppSelector(selectActiveCdd);
  const activeBlueprint = useAppSelector(selectActiveBlueprint);
  const components = useAppSelector(selectBlueprintComponents);
  const activeStyle = useAppSelector(selectActiveStyle);
  const prompts = useAppSelector(selectPrompts);
  const selProject = useAppSelector(selectSelectedProject);
  const selCluster = useAppSelector(selectSelectedCluster);
  const selCourse = useAppSelector(selectSelectedCourse);
  const modelChoice = useAppSelector(selectModelChoice);
  const expertDomain = useAppSelector(selectExpertDomain);
  const targetAudience = useAppSelector(selectTargetAudience);
  const audienceCategory = useAppSelector(selectAudienceCategory);

  const [cddOverrideId, setCddOverrideId] = useState('');
  const [bpOverrideId, setBpOverrideId] = useState('');
  const [allCdds, setAllCdds] = useState([]);
  const [allBps, setAllBps] = useState([]);
  const [selectedCompValue, setSelectedCompValue] = useState('');
  const [promptName, setPromptName] = useState('');
  const [extraInstructions, setExtraInstructions] = useState('');
  const [promptConfig, setPromptConfig] = useState({ systemPrompt: '', userPromptTemplate: '' });
  const [assessmentOverride, setAssessmentOverride] = useState(false);
  const [completionGate, setCompletionGate] = useState(null);
  const [savedInstrs, setSavedInstrs] = useState([]);
  const [loadInstrSel, setLoadInstrSel] = useState('— Start fresh —');
  const [showSaveInstr, setShowSaveInstr] = useState(false);
  const [saveInstrName, setSaveInstrName] = useState('');

  const effCddId = cddOverrideId ? Number(cddOverrideId) : activeCdd?.id;
  const effBpId = bpOverrideId ? Number(bpOverrideId) : activeBlueprint?.id;

  const selectedComponent = useMemo(
    () => components.find((c) => c.value === selectedCompValue),
    [components, selectedCompValue],
  );

  const promptOptions = useMemo(() => {
    const names = [...new Set((prompts || []).map((p) => p.name).filter(Boolean))];
    return [
      { value: '', label: '— Select a template —' },
      ...names.map((n) => ({ value: n, label: n })),
    ];
  }, [prompts]);

  const isReady = Boolean(effCddId && effBpId && selectedComponent && promptName);

  useEffect(() => {
    dispatch(fetchCddsThunk(courseId));
    dispatch(fetchBlueprintsThunk(courseId));
    dispatch(fetchStylesThunk());
    dispatch(fetchPromptsThunk({}));
  }, [courseId, dispatch]);

  useEffect(() => {
    if (effBpId) dispatch(fetchBlueprintComponentsThunk(effBpId));
  }, [effBpId, dispatch]);

  useEffect(() => {
    if (components.length > 0 && !selectedCompValue) {
      setSelectedCompValue(components[0].value);
    }
  }, [components, selectedCompValue]);

  useEffect(() => {
    async function loadOverrides() {
      try {
        const cdds = await cddService.listAllCdds({ page_size: 100 });
        setAllCdds(cdds || []);
        const bps = await blueprintService.listBlueprints({
          courseId: Number(courseId),
          projectId: selProject?.id,
        });
        setAllBps(bps || []);
      } catch {
        setAllCdds([]);
        setAllBps([]);
      }
    }
    loadOverrides();
  }, [courseId, selProject?.id]);

  useEffect(() => {
    async function checkGate() {
      if (!selectedComponent || !effBpId) {
        setCompletionGate(null);
        return;
      }
      try {
        if (selectedComponent.type === 'assessment') {
          const st = await generateService.getModuleCompletion(effBpId);
          setCompletionGate(st);
        } else if (selectedComponent.type === 'course' || selectedComponent.value?.includes('course')) {
          const st = await generateService.getCourseCompletion(Number(courseId));
          setCompletionGate(st);
        } else {
          setCompletionGate(null);
        }
      } catch {
        setCompletionGate(null);
      }
    }
    checkGate();
  }, [selectedComponent, effBpId, courseId]);

  useEffect(() => {
    if (activeJobId && ['pending', 'queued', 'running'].includes(jobStatus)) {
      dispatch(pollJobThunk(activeJobId));
    }
  }, [activeJobId, jobStatus, dispatch]);

  useEffect(() => {
    async function loadSaved() {
      if (!selProject?.id) return;
      try {
        const list = await adminService.listInstructions({
          component: 'generate',
          project_id: selProject.id,
          course_id: Number(courseId),
        });
        setSavedInstrs(list || []);
      } catch {
        setSavedInstrs([]);
      }
    }
    loadSaved();
  }, [selProject?.id, courseId]);

  async function onLaunch() {
    if (!isReady || !selProject?.id) return;
    const payload = {
      course_id: Number(courseId),
      project_id: selProject.id,
      cdd_id: effCddId,
      blueprint_id: effBpId,
      component_value: selectedComponent.value,
      component_label: selectedComponent.label,
      component_type: selectedComponent.type || 'lesson',
      prompt_name: promptName,
      model_choice: modelChoice,
      target_audience: targetAudience,
      expert_domain: expertDomain,
      audience_category: audienceCategory,
      extra_instructions: extraInstructions,
      assessment_override: assessmentOverride,
      system_prompt_override: promptConfig.systemPrompt || undefined,
      user_prompt_override: promptConfig.userPromptTemplate || undefined,
    };
    try {
      const accepted = await dispatch(launchGenerationThunk(payload)).unwrap();
      if (accepted?.job_id) dispatch(pollJobThunk(accepted.job_id));
    } catch {
      /* error in slice */
    }
  }

  function handleDownloadPrompt() {
    const md = buildPromptDownloadMd({
      projectName: selProject?.name,
      clusterName: selCluster?.name,
      courseName: selCourse?.name,
      component: 'generate',
      promptName: promptName || 'active',
      promptVersion: 'active',
      systemPrompt: promptConfig.systemPrompt,
      userPromptTemplate: promptConfig.userPromptTemplate,
      extraInstructions,
    });
    downloadBlob(new Blob([md], { type: 'application/msword' }), 'prompt_generate_active.doc');
  }

  const cddDisp = activeCdd
    ? `${activeCdd.title || activeCdd.course_title} (${activeCdd.active_version || 'v1'})`
    : 'None linked';
  const bpDisp = activeBlueprint
    ? `${activeBlueprint.title} (${activeBlueprint.active_version || 'v1'})`
    : 'None linked';
  const styleDisp = activeStyle?.name || 'None';

  const needsOverride = completionGate && !completionGate.completed
    && selectedComponent?.type === 'assessment';

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Generate' }]} noPadding>
      <div className={styles.page}>
        <SectionBadge
          icon="⚙️"
          title="Content Generation"
          subtitle="AI pipeline for generating lessons and course components. Context is auto-injected from the active CDD and Blueprint."
        />

        <div className={styles.stepper}>
          {[
            { icon: '📘', label: 'Pin CDD', done: Boolean(activeCdd) },
            { icon: '🧩', label: 'Pin Blueprint', done: Boolean(activeBlueprint) },
            { icon: '⚙️', label: 'Generate', done: Boolean(activeCdd && activeBlueprint) },
            { icon: '✏️', label: 'Edit', done: false },
          ].map((step, i, arr) => (
            <span key={step.label} style={{ display: 'contents' }}>
              <div className={`${styles.step} ${step.done ? styles['step--done'] : ''}`}>
                <div className={styles.step__icon}>{step.icon}</div>
                <div className={styles.step__label}>{step.label}</div>
                <div className={styles.step__status}>{step.done ? '✓' : '○'}</div>
              </div>
              {i < arr.length - 1 && <span className={styles.stepArrow} aria-hidden="true">→</span>}
            </span>
          ))}
        </div>

        <div className={styles.contextBanner}>
          <div className={styles.contextBanner__col}>
            <div className={styles.contextBanner__heading}>📘 Active CDD</div>
            <div className={styles.contextBanner__value}>
              <span className={styles.contextBanner__dot} style={{ background: activeCdd ? '#10b981' : '#ef4444' }} />
              {cddDisp}
            </div>
          </div>
          <div className={styles.contextBanner__divider} />
          <div className={styles.contextBanner__col}>
            <div className={styles.contextBanner__heading}>🧩 Active Blueprint</div>
            <div className={styles.contextBanner__value}>
              <span className={styles.contextBanner__dot} style={{ background: activeBlueprint ? '#10b981' : '#ef4444' }} />
              {bpDisp}
            </div>
          </div>
          <div className={styles.contextBanner__divider} />
          <div className={styles.contextBanner__col}>
            <div className={styles.contextBanner__heading}>🎨 Active Style</div>
            <div className={styles.contextBanner__value}>
              <span className={styles.contextBanner__dot} style={{ background: activeStyle ? '#10b981' : '#ef4444' }} />
              {styleDisp}
            </div>
          </div>
          <div className={styles.contextBanner__hint}>Set via Style / CDD / Blueprint tabs</div>
        </div>

        <details className={styles.overridePanel}>
          <summary>🔀 Override Active CDD / Blueprint (optional)</summary>
          <p style={{ fontSize: '0.8rem', color: '#6b7280', margin: '8px 0' }}>
            By default the pinned CDD and Blueprint are used. Change here for this generation only.
          </p>
          <div className={styles.overridePanel__grid}>
            <Select
              label="CDD Override"
              options={[
                { value: '', label: '— Use pinned CDD —' },
                ...allCdds.map((c) => ({
                  value: String(c.id),
                  label: `${c.title || c.course_title} (${c.active_version || 'v1'})`,
                })),
              ]}
              value={cddOverrideId}
              onChange={(e) => setCddOverrideId(e.target.value)}
            />
            <Select
              label="Blueprint Override"
              options={[
                { value: '', label: '— Use pinned Blueprint —' },
                ...allBps.map((b) => ({
                  value: String(b.id),
                  label: `${b.title} (${b.active_version || 'v1'})`,
                })),
              ]}
              value={bpOverrideId}
              onChange={(e) => setBpOverrideId(e.target.value)}
            />
          </div>
        </details>

        <div className={styles.layout}>
          <section className={styles.panel}>
            <div className={styles.sectionLabel}>📋 Content Type</div>
            {components.length > 0 ? (
              <>
                <div className={styles.compOk}>
                  ✅ <strong>{components.length} component(s)</strong> loaded from Blueprint — Content Type dropdown auto-populated.
                </div>
                <Select
                  label="Content Type"
                  options={components.map((c) => ({ value: c.value, label: c.label }))}
                  value={selectedCompValue}
                  onChange={(e) => setSelectedCompValue(e.target.value)}
                />
                {selectedComponent && (
                  <p style={{ fontSize: '0.8rem', color: '#6b7280', margin: 0 }}>
                    🧩 {activeBlueprint?.module_number ? `M${activeBlueprint.module_number} — ` : ''}
                    {selectedComponent.label} · type: {selectedComponent.type}
                  </p>
                )}
              </>
            ) : effBpId ? (
              <div className={styles.compWarn}>
                ⚠️ <strong>Blueprint has no sections yet.</strong> Generate or edit it in the Blueprint tab first.
              </div>
            ) : (
              <div className={styles.compWarn}>
                ⚠️ <strong>No Blueprint pinned.</strong> Pin a Blueprint via the Blueprint tab first.
              </div>
            )}

            <div className={styles.sectionLabel} style={{ marginTop: 16 }}>🛠️ Prompt & Source</div>
            <Select
              label="Prompt Template"
              options={promptOptions}
              value={promptName}
              onChange={(e) => setPromptName(e.target.value)}
              disabled={!selectedComponent}
            />

            {needsOverride && (
              <div className={styles.gateWarn}>
                ⚠️ Not all lessons are complete ({completionGate.generated_lessons}/{completionGate.total_lessons}).
                <label className={styles.checkLabel} style={{ marginTop: 8 }}>
                  <input
                    type="checkbox"
                    checked={assessmentOverride}
                    onChange={(e) => setAssessmentOverride(e.target.checked)}
                  />
                  Generate assessment anyway (override)
                </label>
              </div>
            )}

            {savedInstrs.length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8 }}>
                <Select
                  label="Saved instructions"
                  options={[
                    { value: '— Start fresh —', label: '— Start fresh —' },
                    ...savedInstrs.map((r) => ({ value: r.name, label: r.name })),
                  ]}
                  value={loadInstrSel}
                  onChange={(e) => setLoadInstrSel(e.target.value)}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => {
                    if (loadInstrSel === '— Start fresh —') setExtraInstructions('');
                    else {
                      const rec = savedInstrs.find((r) => r.name === loadInstrSel);
                      if (rec) setExtraInstructions(rec.content);
                    }
                  }}
                >
                  📥 Load
                </Button>
              </div>
            )}
            <textarea
              className={styles.form}
              rows={3}
              placeholder="e.g. Use real-world case studies. Add knowledge check questions."
              value={extraInstructions}
              onChange={(e) => setExtraInstructions(e.target.value)}
              style={{ resize: 'vertical', width: '100%', padding: 8, borderRadius: 8, border: '1px solid #e5e7eb' }}
            />

            {generateError && <ErrorState message={generateError} />}
          </section>

          <section className={styles.panel}>
            <h2 className={styles.panel__title}>
              {activeJobId ? 'Generation Progress' : 'Latest Results'}
            </h2>
            {activeJobId && (
              <div className={styles.jobProgress}>
                <div className={styles.jobProgress__status}>
                  {['pending', 'queued', 'running'].includes(jobStatus) && (
                    <><Loader size="sm" /> {jobStatus === 'running' ? 'Running…' : 'Queued…'}</>
                  )}
                  {(jobStatus === JOB_STATUSES.COMPLETED || jobStatus === 'completed') && '✅ Completed'}
                  {(jobStatus === JOB_STATUSES.FAILED || jobStatus === 'failed') && '❌ Failed'}
                </div>
                {jobProgress.map((msg, i) => (
                  <div key={i} className={styles.jobProgress__stage}>{msg}</div>
                ))}
                {!['completed', JOB_STATUSES.COMPLETED].includes(jobStatus) && (
                  <Button variant="ghost" size="sm" onClick={() => dispatch(clearJob())}>Dismiss</Button>
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
                      {(block.content || '').slice(0, 200)}
                      {block.content?.length > 200 ? '…' : ''}
                    </p>
                  </div>
                ))}
              </div>
            )}
            {!activeJobId && latestBlocks.length === 0 && (
              <div className={styles.empty}>
                <span aria-hidden="true">✨</span>
                <p>Generated blocks will appear here after you launch the pipeline.</p>
              </div>
            )}
          </section>
        </div>

        <hr className={styles.divider} />

        <InlinePromptControls
          component="generate"
          extraInstructions={extraInstructions}
          showExtraInstructions={false}
          onPromptsChange={setPromptConfig}
          headerHint="📝 Select a content type and prompt template above, then configure the prompt and launch generation below."
        />

        <div className={styles.generateRow}>
          <Button
            variant="primary"
            size="lg"
            className={styles.generateRow__main}
            loading={isGenerating}
            disabled={!isReady || (needsOverride && !assessmentOverride)}
            onClick={onLaunch}
          >
            {isGenerating ? 'Launching…' : '🚀 Launch Pipeline'}
          </Button>
          <Button variant="secondary" size="lg" onClick={handleDownloadPrompt}>
            ⬇️ Download Prompt
          </Button>
        </div>
      </div>
    </PageContainer>
  );
}
