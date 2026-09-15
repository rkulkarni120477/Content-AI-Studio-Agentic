import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { launchGenerationThunk, pollJobThunk, cancelJobThunk } from '@features/generate/generateThunks';
import {
  selectIsGenerating, selectActiveJobId, selectJobCourseId, selectJobStatus,
  selectJobProgress, selectJobProgressPct, selectJobErrorDetail,
  selectLatestBlocks, selectGenerateError,
  clearJob, clearError,
} from '@features/generate/generateSlice';
import { selectActiveBlueprint, selectBlueprintComponents, selectBlueprints } from '@features/blueprint/blueprintSlice';
import { selectActiveCdd, selectCdds } from '@features/cdd/cddSlice';
import { fetchBlueprintComponentsThunk } from '@features/blueprint/blueprintThunks';
import { fetchBlueprintsThunk } from '@features/blueprint/blueprintThunks';
import { fetchCddsThunk } from '@features/cdd/cddThunks';
import { fetchStylesThunk } from '@features/style/styleThunks';
import { selectActiveStyle } from '@features/style/styleSlice';
import {
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  selectModelChoice, selectExpertDomain, selectTargetAudience, selectAudienceCategory,
} from '@features/dashboard/dashboardSlice';
import { adminService } from '@features/admin/services/adminService';
import { generateService } from '@features/generate/services/generateService';
import { documentService } from '@features/documents/documentService';
import { cddService } from '@features/cdd/services/cddService';
import { blueprintService } from '@features/blueprint/services/blueprintService';
import {
  canLaunchWithModuleGate,
  isCourseLevelComponent,
  isModuleAssessmentLabel,
  isModuleLevelComponent,
  shouldShowAssessmentOverride,
} from '@utils/generationGates';
import { JOB_STATUSES, isTerminalJobStatus } from '@utils/constants';
import { renderMarkdownPreview } from '@utils/markdownPreview';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import InlinePromptControls from '@components/generation/InlinePromptControls/InlinePromptControls';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Loader from '@components/common/Loader/Loader';
import ErrorState from '@components/common/ErrorState/ErrorState';

import { useLabels } from '@hooks/useLabels';
import styles from './GeneratePage.module.scss';

const SUPP_UPLOADS = [
  { key: 'guidelines', label: 'Guidelines (PDF/TXT)', accept: '.pdf,.txt', sourceType: 'guidelines' },
  { key: 'checklist', label: 'Checklist (PDF/XLSX/TXT)', accept: '.pdf,.xlsx,.txt', sourceType: 'checklist' },
  { key: 'chapter', label: 'Chapter / Source Material (PDF/DOCX/TXT)', accept: '.pdf,.docx,.txt', sourceType: 'chapter' },
];

export default function GeneratePage() {
  const { courseId } = useParams();
  const numericCourseId = Number(courseId);
  const dispatch = useAppDispatch();
  const L = useLabels();

  const isGenerating = useAppSelector(selectIsGenerating);
  const activeJobId = useAppSelector(selectActiveJobId);
  const jobCourseId = useAppSelector(selectJobCourseId);
  const jobStatus = useAppSelector(selectJobStatus);
  const jobProgress = useAppSelector(selectJobProgress);
  const jobProgressPct = useAppSelector(selectJobProgressPct);
  const jobErrorDetail = useAppSelector(selectJobErrorDetail);
  const latestBlocks = useAppSelector(selectLatestBlocks);
  const generateError = useAppSelector(selectGenerateError);
  const activeCdd = useAppSelector(selectActiveCdd);
  const activeBlueprint = useAppSelector(selectActiveBlueprint);
  const cddsFromStore = useAppSelector(selectCdds);
  const blueprintsFromStore = useAppSelector(selectBlueprints);
  const components = useAppSelector(selectBlueprintComponents);
  const activeStyle = useAppSelector(selectActiveStyle);
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
  const [extraInstructions, setExtraInstructions] = useState('');
  const [promptConfig, setPromptConfig] = useState({ systemPrompt: '', userPromptTemplate: '', hasOverride: false, selectedPromptId: null, isDefault: true });
  const [assessmentOverride, setAssessmentOverride] = useState(false);
  const [moduleGate, setModuleGate] = useState(null);
  const [courseGate, setCourseGate] = useState(null);
  const [suppFiles, setSuppFiles] = useState({ guidelines: null, checklist: null, chapter: null });
  const [suppErrors, setSuppErrors] = useState({});
  const [parsingSupp, setParsingSupp] = useState(false);
  const [showSuppPanel, setShowSuppPanel] = useState(false);

  const effCddId = cddOverrideId ? Number(cddOverrideId) : activeCdd?.id;
  const effBpId = bpOverrideId ? Number(bpOverrideId) : activeBlueprint?.id;

  const selectedComponent = useMemo(
    () => components.find((c) => c.value === selectedCompValue),
    [components, selectedCompValue],
  );

  const overrideCdds = useMemo(() => {
    const byId = new Map();
    [...allCdds, ...cddsFromStore].forEach((c) => {
      if (c?.id) byId.set(c.id, c);
    });
    return Array.from(byId.values());
  }, [allCdds, cddsFromStore]);

  const overrideBps = useMemo(() => {
    const byId = new Map();
    [...allBps, ...blueprintsFromStore].forEach((b) => {
      if (b?.id) byId.set(b.id, b);
    });
    return Array.from(byId.values());
  }, [allBps, blueprintsFromStore]);

  const cddOverrideOptions = useMemo(
    () => [
      { value: '', label: `— Use pinned ${L.cdd} —` },
      ...overrideCdds.map((c) => ({
        value: String(c.id),
        label: `${c.title || c.course_title || `${L.cdd} ${c.id}`} (${c.active_version || 'v1'})`,
      })),
    ],
    [overrideCdds, L.cdd],
  );

  const bpOverrideOptions = useMemo(
    () => [
      { value: '', label: `— Use pinned ${L.blueprint} —` },
      ...overrideBps.map((b) => ({
        value: String(b.id),
        label: `${b.title || `${L.blueprint} ${b.id}`} (${b.active_version || 'v1'})`,
      })),
    ],
    [overrideBps, L.blueprint],
  );

  const showAssessmentGate = shouldShowAssessmentOverride(moduleGate, selectedComponent);
  const showCourseGate = Boolean(
    selectedComponent
    && isCourseLevelComponent(selectedComponent.value)
    && courseGate
    && !courseGate.completed,
  );

  const moduleLaunchAllowed = canLaunchWithModuleGate(
    moduleGate,
    selectedComponent?.label,
    assessmentOverride,
  );

  const isReady = Boolean(effCddId && effBpId && selectedComponent);
  const launchDisabled = !isReady
    || !moduleLaunchAllowed
    || (showCourseGate && !courseGate?.completed)
    || isGenerating;

  useEffect(() => {
    dispatch(fetchCddsThunk(courseId));
    dispatch(fetchBlueprintsThunk(courseId));
    dispatch(fetchStylesThunk());
  }, [courseId, dispatch]);

  useEffect(() => {
    if (effBpId) {
      setSelectedCompValue('');
      dispatch(fetchBlueprintComponentsThunk(effBpId));
    }
  }, [effBpId, dispatch]);

  useEffect(() => {
    if (components.length > 0 && !selectedCompValue) {
      setSelectedCompValue(components[0].value);
    }
  }, [components, selectedCompValue]);


  useEffect(() => {
    async function loadOverrides() {
      try {
        const [cdds, bps] = await Promise.all([
          cddService.listAllCdds({ page_size: 200 }),
          blueprintService.listAllBlueprints(),
        ]);
        setAllCdds(cdds || []);
        setAllBps(bps || []);
      } catch {
        /* fall back to lists already loaded for this course */
      }
    }
    loadOverrides();
  }, [courseId]);

  useEffect(() => {
    setAssessmentOverride(false);
    async function checkGates() {
      if (!selectedComponent) {
        setModuleGate(null);
        setCourseGate(null);
        return;
      }
      try {
        if (effBpId && isModuleLevelComponent(selectedComponent.value)) {
          const st = await generateService.getModuleCompletion(effBpId);
          setModuleGate(st);
        } else {
          setModuleGate(null);
        }
        if (effCddId && isCourseLevelComponent(selectedComponent.value)) {
          const st = await generateService.getCourseCompletion(Number(courseId), effCddId);
          setCourseGate(st);
        } else {
          setCourseGate(null);
        }
      } catch {
        setModuleGate(null);
        setCourseGate(null);
      }
    }
    checkGates();
  }, [selectedComponent, effBpId, effCddId, courseId]);

  // Only the title the job was launched on resumes polling — a different title
  // must not poll (and thereby pull) another title's job into view.
  const jobBelongsToThisCourse = jobCourseId != null && jobCourseId === numericCourseId;

  useEffect(() => {
    if (jobBelongsToThisCourse && activeJobId && jobStatus && !isTerminalJobStatus(jobStatus)) {
      dispatch(pollJobThunk({ jobId: activeJobId }));
    }
  }, [jobBelongsToThisCourse, activeJobId, jobStatus, dispatch]);

  async function handleSuppFile(key, files, sourceType) {
    const file = files?.[0];
    if (!file) {
      setSuppFiles((s) => ({ ...s, [key]: null }));
      return;
    }
    setParsingSupp(true);
    setSuppErrors((e) => ({ ...e, [key]: null }));
    try {
      const parsed = await documentService.parseFile(file, sourceType);
      setSuppFiles((s) => ({
        ...s,
        [key]: { name: parsed.name, content: parsed.content, source_type: sourceType },
      }));
    } catch (err) {
      setSuppErrors((e) => ({ ...e, [key]: err?.message || 'Could not parse file' }));
      setSuppFiles((s) => ({ ...s, [key]: null }));
    } finally {
      setParsingSupp(false);
    }
  }

  async function onLaunch() {
    if (launchDisabled || !selProject?.id) return;

    const supplementary_files = Object.values(suppFiles).filter(Boolean);

    // DLU day components carry their own blueprint_id (each day is a separate
    // blueprint) and normalise to the 'dlu_day' value the backend uses to
    // inject that day's plan as context. Standard components are unchanged.
    const dluBpId = selectedComponent.metadata?.blueprint_id;
    const isDluComponent = selectedComponent.metadata?.structure === 'dlu'
      || String(selectedComponent.value || '').startsWith('dlu_day');
    const genBlueprintId = isDluComponent && dluBpId ? Number(dluBpId) : effBpId;

    const payload = {
      course_id: Number(courseId),
      project_id: selProject.id,
      cdd_id: effCddId,
      blueprint_id: genBlueprintId,
      component_value: isDluComponent ? 'dlu_day' : selectedComponent.value,
      component_label: selectedComponent.label,
      component_type: selectedComponent.type || 'lesson',
      model_choice: modelChoice,
      target_audience: targetAudience,
      expert_domain: expertDomain,
      audience_category: audienceCategory,
      extra_instructions: extraInstructions,
      assessment_override: assessmentOverride,
      // Day-scoped structured grounding (§7): a DLU day component's own
      // metadata already carries its day_number (parse_blueprint_components,
      // blueprint_parser.py) -- this was resolved but never sent, so Generate
      // always fell through to the free-text blob query for a DLU day. No
      // block label sent from here; the server derives it from the course/CDD
      // title when day is present and block is not.
      day: selectedComponent.metadata?.day_number ?? undefined,
      context_document_names: [],
      supplementary_files,
      // Non-default library prompt selection drives content generation server-side.
      prompt_id: (!promptConfig.hasOverride && !promptConfig.isDefault && promptConfig.selectedPromptId)
        ? promptConfig.selectedPromptId
        : undefined,
    };

    try {
      const accepted = await dispatch(launchGenerationThunk(payload)).unwrap();
      if (extraInstructions.trim()) {
        await adminService.saveInstruction({
          name: (selectedComponent.label || 'Generate Instructions').slice(0, 80),
          component: 'generate',
          content: extraInstructions.trim(),
          project_id: selProject.id,
          course_id: Number(courseId),
        }).catch(() => {});
      }
      if (accepted?.job_id) dispatch(pollJobThunk({ jobId: accepted.job_id }));
    } catch {
      /* error in slice */
    }
  }

  const cddDisp = activeCdd
    ? `${activeCdd.title || activeCdd.course_title} (${activeCdd.active_version || 'v1'})`
    : 'None linked';
  const bpDisp = activeBlueprint
    ? `${activeBlueprint.title} (${activeBlueprint.active_version || 'v1'})`
    : 'None linked';
  const styleDisp = activeStyle?.name || 'None';

  // Job + results only belong on the title that launched them. On any other
  // title (existing or brand-new) these read empty, so the page shows the normal
  // interface; returning to the owning title surfaces them again — including a
  // result that completed in the background while the user was away.
  const activeJobIdForCourse = jobBelongsToThisCourse ? activeJobId : null;
  const latestBlocksForCourse = jobBelongsToThisCourse ? latestBlocks : [];
  const jobActive = activeJobIdForCourse && jobStatus && !isTerminalJobStatus(jobStatus);
  const jobTerminal = activeJobIdForCourse && jobStatus && isTerminalJobStatus(jobStatus);
  const showJobOnly = jobTerminal && latestBlocksForCourse.length === 0 && !generateError;

  function handleStartAnother() {
    dispatch(clearJob());
    dispatch(clearError());
  }

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Generate' }]} noPadding>
      <div className={styles.page}>
        <SectionBadge
          icon="⚙️"
          title="Content Generation"
          subtitle={`AI pipeline for generating lessons and ${L.titleLower} components. Context is auto-injected from the active ${L.cdd} and ${L.blueprint}.`}
        />

        {jobActive && (
          <div className={styles.activeJob}>
            <div className={styles.activeJob__header}>
              <Loader size="sm" />
              <span>
                Generating content for{' '}
                <strong>{selectedComponent?.label || 'selected lesson'}</strong>…
              </span>
              {activeJobIdForCourse && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={jobStatus === JOB_STATUSES.CANCELLED}
                  onClick={() => dispatch(cancelJobThunk(activeJobIdForCourse))}
                >
                  Cancel
                </Button>
              )}
            </div>
            <div className={styles.progressBar} role="progressbar" aria-valuenow={jobProgressPct} aria-valuemin={0} aria-valuemax={100}>
              <div className={styles.progressBar__fill} style={{ width: `${Math.min(100, jobProgressPct)}%` }} />
            </div>
            <div className={styles.activeJob__pct}>{jobProgressPct}%</div>
          </div>
        )}

        {!showJobOnly && (
        <>
        <div className={styles.stepper}>
          {[
            { icon: '📘', label: `Pin ${L.cdd}`, done: Boolean(activeCdd) },
            { icon: '🧩', label: `Pin ${L.blueprint}`, done: Boolean(activeBlueprint) },
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
            <div className={styles.contextBanner__heading}>📘 Active {L.cdd}</div>
            <div className={styles.contextBanner__value}>
              <span className={styles.contextBanner__dot} style={{ background: activeCdd ? '#10b981' : '#ef4444' }} />
              {cddDisp}
            </div>
          </div>
          <div className={styles.contextBanner__divider} />
          <div className={styles.contextBanner__col}>
            <div className={styles.contextBanner__heading}>🧩 Active {L.blueprint}</div>
            <div className={styles.contextBanner__value}>
              <span className={styles.contextBanner__dot} style={{ background: activeBlueprint ? '#10b981' : '#ef4444' }} />
              {bpDisp}
            </div>
          </div>
          <div className={styles.contextBanner__divider} />
          <div className={styles.contextBanner__col}>
            <div className={styles.contextBanner__heading}>🎨 Active {L.style}</div>
            <div className={styles.contextBanner__value}>
              <span className={styles.contextBanner__dot} style={{ background: activeStyle ? '#10b981' : '#ef4444' }} />
              {styleDisp}
            </div>
          </div>
          <div className={styles.contextBanner__hint}>Set via {L.style} / {L.cdd} / {L.blueprint} tabs</div>
        </div>

        <div className={styles.sectionStack}>
        <details className={styles.overridePanel}>
          <summary>🔀 Override Active {L.cdd} / {L.blueprint} (optional)</summary>
          <p className={styles.overridePanel__caption}>
            By default the pinned {L.cdd} and {L.blueprint} are used. Change here for this generation only — does not affect the pin.
          </p>
          <div className={styles.overridePanel__grid}>
            <Select
              label={`${L.cdd} Override`}
              options={cddOverrideOptions}
              value={cddOverrideId}
              onChange={(e) => setCddOverrideId(e.target.value)}
            />
            <Select
              label={`${L.blueprint} Override`}
              options={bpOverrideOptions}
              value={bpOverrideId}
              onChange={(e) => setBpOverrideId(e.target.value)}
            />
          </div>
        </details>

        {components.length > 0 && (
          <div className={styles.compOk}>
            ✅ <strong>{components.length} component(s)</strong> loaded from {L.blueprint} — Content Type dropdown auto-populated.
          </div>
        )}

        <div className={styles.promptWrap}>
          <InlinePromptControls
            component="generate"
            extraInstructions={extraInstructions}
            onExtraInstructionsChange={setExtraInstructions}
            onPromptsChange={setPromptConfig}
            showExtraInstructions
          />
        </div>

        <section className={styles.configCard}>
            <div className={styles.formGrid}>
              <div>
                <div className={styles.sectionLabel}>📋 Content Type</div>
                {components.length > 0 ? (
                  <>
                    <Select
                      label="Content Type"
                      options={components.map((c) => ({ value: c.value, label: c.label }))}
                      value={selectedCompValue}
                      onChange={(e) => setSelectedCompValue(e.target.value)}
                    />
                    {selectedComponent && (
                      <p className={styles.compCaption}>
                        🧩 {(selectedComponent.metadata?.structure !== 'dlu' && activeBlueprint?.module_number)
                          ? `M${activeBlueprint.module_number} — ` : ''}
                        {selectedComponent.label} · type: <code>{selectedComponent.type}</code>
                      </p>
                    )}
                  </>
                ) : effBpId ? (
                  <div className={styles.compWarn}>
                    ⚠️ <strong>{L.blueprint} has no sections yet.</strong> Generate or edit it in the {L.blueprint} tab first.
                  </div>
                ) : (
                  <div className={styles.compWarn}>
                    ⚠️ <strong>No {L.blueprint} pinned.</strong> Pin a {L.blueprint} via the {L.blueprint} tab first.
                    The Content Type dropdown will auto-populate from lessons and components defined in it.
                  </div>
                )}
              </div>

            </div>

            <hr className={styles.formDivider} />

            {selectedComponent && (
              <details
                className={styles.suppPanel}
                open={showSuppPanel}
                onToggle={(e) => setShowSuppPanel(e.target.open)}
              >
                <summary>📂 Supplementary File Uploads (optional)</summary>
                <p className={styles.suppPanel__caption}>
                  Upload extra reference files in addition to {L.cdd}/{L.blueprint} context.
                </p>
                <div className={styles.suppPanel__grid}>
                  {SUPP_UPLOADS.map(({ key, label, accept, sourceType }) => (
                    <div key={key}>
                      <span className={styles.suppPanel__fileLabel}>{label}</span>
                      <FileUpload
                        accept={accept}
                        label="Drop file or click to browse"
                        disabled={parsingSupp}
                        onChange={(files) => handleSuppFile(key, files, sourceType)}
                      />
                      {suppFiles[key] && (
                        <p className={styles.suppPanel__ok}>✓ {suppFiles[key].name}</p>
                      )}
                      {suppErrors[key] && (
                        <p className={styles.suppPanel__err} role="alert">{suppErrors[key]}</p>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            )}

            {moduleGate?.completed && selectedComponent && isModuleLevelComponent(selectedComponent.value) && (
              <div className={styles.gateOk}>
                ✅ All {moduleGate.total_lessons} lesson(s) completed for this module. <strong>{selectedComponent.label}</strong> generation is unlocked.
              </div>
            )}

            {showAssessmentGate && (
              <div className={styles.gateWarn}>
                <strong>⚠️ Not all lessons have been generated yet</strong>
                <p>
                  Only <strong>{moduleGate.generated_lessons} of {moduleGate.total_lessons}</strong> lesson(s) are complete
                  for this module. The assessment may lack full context.
                </p>
                <label className={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={assessmentOverride}
                    onChange={(e) => setAssessmentOverride(e.target.checked)}
                  />
                  Generate Anyway — I understand not all lessons are complete
                </label>
              </div>
            )}

            {selectedComponent
              && isModuleLevelComponent(selectedComponent.value)
              && moduleGate
              && !moduleGate.completed
              && !isModuleAssessmentLabel(selectedComponent?.label)
              && (
                <div className={styles.gateBlock}>
                  <strong>🚫 Module lessons are incomplete</strong>
                  <p>Complete all lessons before generating module-level components.</p>
                  <div className={styles.gateBlock__bar}>
                    <div
                      className={styles.gateBlock__fill}
                      style={{
                        width: `${moduleGate.total_lessons
                          ? Math.round((moduleGate.generated_lessons / moduleGate.total_lessons) * 100)
                          : 0}%`,
                      }}
                    />
                  </div>
                  <span className={styles.gateBlock__count}>
                    {moduleGate.generated_lessons} / {moduleGate.total_lessons} lessons generated
                  </span>
                </div>
              )}

            {showCourseGate && (
              <div className={styles.gateBlock}>
                <strong>🚫 {L.title} modules are incomplete</strong>
                <p>
                  Complete all modules ({courseGate.generated_lessons}/{courseGate.total_lessons}) before generating{' '}
                  <strong>{selectedComponent.label}</strong>.
                </p>
              </div>
            )}

            {generateError && <ErrorState message={generateError} />}

            <div className={styles.launchRow}>
              <Button
                variant="primary"
                size="lg"
                className={styles.launchRow__btn}
                loading={isGenerating}
                disabled={launchDisabled}
                onClick={onLaunch}
              >
                {isGenerating ? 'Generating…' : '🚀 Generate'}
              </Button>
            </div>

            {!selectedComponent && (
              <p className={styles.validationWarn}>⚠️ Select a Content Type to continue.</p>
            )}
        </section>

        {!activeJobIdForCourse && latestBlocksForCourse.length === 0 && (
          components.length > 0 ? (
            <div className={styles.readyState}>
              ✅ <strong>{L.blueprint} loaded</strong> — {components.length} component(s) ready.
              Select a component and click <strong>🚀 Generate</strong>.
            </div>
          ) : (
            <div className={styles.configureEmpty}>
              <span className={styles.configureEmpty__icon} aria-hidden="true">⚙️</span>
              <h3 className={styles.configureEmpty__title}>Configure your generation</h3>
              <p className={styles.configureEmpty__text}>
                Pin a <strong>{L.cdd}</strong> and <strong>{L.blueprint}</strong> using the tabs above,
                then the Content Type dropdown will auto-populate with all {L.blueprint} components.
              </p>
            </div>
          )
        )}
        </div>
        </>
        )}

        {(activeJobIdForCourse || latestBlocksForCourse.length > 0) && (
          <section className={styles.resultsPanel}>
            <h2 className={styles.resultsPanel__title}>
              {jobActive ? 'Generation Progress' : 'Latest Results'}
            </h2>
            {jobTerminal && !jobActive && (
              <div className={styles.jobProgress}>
                <div className={styles.jobProgress__status}>
                  {(jobStatus === JOB_STATUSES.COMPLETED || jobStatus === 'completed') && '✅ Generation complete'}
                  {(jobStatus === JOB_STATUSES.FAILED || jobStatus === 'failed') && '❌ Generation failed'}
                  {jobStatus === 'cancelled' && '⏹ Cancelled'}
                </div>
                {jobErrorDetail && (
                  <details className={styles.jobErrorDetail}>
                    <summary>Error details</summary>
                    <pre>{jobErrorDetail}</pre>
                  </details>
                )}
                <div className={styles.jobActions}>
                  {(jobStatus === JOB_STATUSES.FAILED || jobStatus === 'failed') && (
                    <Button variant="primary" size="sm" onClick={handleStartAnother}>Try Again</Button>
                  )}
                  {(jobStatus === JOB_STATUSES.COMPLETED || jobStatus === 'completed' || jobStatus === 'cancelled') && (
                    <Button variant="primary" size="sm" onClick={handleStartAnother}>Generate Another</Button>
                  )}
                  <Button variant="ghost" size="sm" onClick={() => dispatch(clearJob())}>Dismiss</Button>
                </div>
              </div>
            )}
            {latestBlocksForCourse.length > 0 && (
              <div className={styles.results}>
                <p className={styles.results__count}>
                  ✅ {latestBlocksForCourse.length} block(s) generated
                </p>
                {latestBlocksForCourse.map((block, i) => (
                  <details key={block.id || i} className={styles.blockCard}>
                    <summary className={styles.blockCard__header}>
                      <span className={styles.blockCard__label}>{block.block_label || `Block ${i + 1}`}</span>
                    </summary>
                    {block.content || block.content_preview
                      ? (
                        <div
                          className={`${styles.blockCard__content} markdown-content`}
                          dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(block.content || block.content_preview) }}
                        />
                      )
                      : <p className={styles.blockCard__tip}>Content saved — open the <strong>Editor</strong> tab to view and edit.</p>
                    }
                  </details>
                ))}
                {!jobActive && (
                  <Button variant="secondary" size="sm" onClick={handleStartAnother}>
                    Generate Another
                  </Button>
                )}
              </div>
            )}
          </section>
        )}
      </div>
    </PageContainer>
  );
}
