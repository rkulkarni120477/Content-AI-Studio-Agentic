import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchCddsThunk, generateCddThunk, setActiveCddThunk,
  fetchCddVersionsThunk, commitCddVersionThunk, exportCddThunk,
  activateCddVersionThunk,
} from '@features/cdd/cddThunks';
import { cddService } from '@features/cdd/services/cddService';
import {
  selectCdds, selectActiveCdd, selectCddVersions,
  selectCddLoading, selectCddGenerating, selectCddError,
} from '@features/cdd/cddSlice';
import {
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  selectModelChoice, selectExpertDomain, selectTargetAudience, selectAudienceCategory,
} from '@features/dashboard/dashboardSlice';
import { selectStyles, selectActiveStyle } from '@features/style/styleSlice';
import { fetchStylesThunk } from '@features/style/styleThunks';
import { adminService } from '@features/admin/services/adminService';
import { buildPromptDownloadMd } from '@utils/promptDefaults';
import { createCddSchema, commitVersionSchema } from '@utils/validation';
import { downloadBlob, formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import InlinePromptControls from '@components/generation/InlinePromptControls/InlinePromptControls';
import CddContentView from '@components/cdd/CddContentView/CddContentView';
import { patchCddBlock } from '@utils/cddContent';
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
  const dispatch = useAppDispatch();
  const cdds = useAppSelector(selectCdds);
  const activeCdd = useAppSelector(selectActiveCdd);
  const versions = useAppSelector(selectCddVersions);
  const selProject = useAppSelector(selectSelectedProject);
  const selCluster = useAppSelector(selectSelectedCluster);
  const selCourse = useAppSelector(selectSelectedCourse);
  const stylesList = useAppSelector(selectStyles);
  const activeStyle = useAppSelector(selectActiveStyle);
  const modelChoice = useAppSelector(selectModelChoice);
  const expertDomain = useAppSelector(selectExpertDomain);
  const targetAudience = useAppSelector(selectTargetAudience);
  const audienceCategory = useAppSelector(selectAudienceCategory);
  const isLoading = useAppSelector(selectCddLoading);
  const isGenerating = useAppSelector(selectCddGenerating);
  const error = useAppSelector(selectCddError);

  const [selectedStyleId, setSelectedStyleId] = useState(null);
  const [extraInstructions, setExtraInstructions] = useState('');
  const [promptConfig, setPromptConfig] = useState({ systemPrompt: '', userPromptTemplate: '' });
  const [savedInstrs, setSavedInstrs] = useState([]);
  const [loadInstrSel, setLoadInstrSel] = useState('— Start fresh —');
  const [showSaveInstr, setShowSaveInstr] = useState(false);
  const [saveInstrName, setSaveInstrName] = useState('');
  const [saveInstrNewVer, setSaveInstrNewVer] = useState(false);
  const [viewCddId, setViewCddId] = useState(null);
  const [showVersionModal, setShowVersionModal] = useState(false);
  const [selectedCddId, setSelectedCddId] = useState(null);
  const [viewVersion, setViewVersion] = useState(null);
  const [versionDetail, setVersionDetail] = useState(null);
  const [savingBlock, setSavingBlock] = useState(false);

  const generateForm = useForm({
    resolver: zodResolver(createCddSchema),
    defaultValues: { course_title: '', document_title: '', duration_hours: 8 },
  });
  const versionForm = useForm({ resolver: zodResolver(commitVersionSchema) });

  const displayCdd = viewCddId
    ? (cdds.find((c) => c.id === viewCddId) || (activeCdd?.id === viewCddId ? activeCdd : null))
    : activeCdd;

  const styleOptions = useMemo(() => {
    const opts = [{ value: '', label: '— No style —' }];
    const mostRecent = stylesList[0];
    stylesList.forEach((s) => {
      let tag = '';
      if (s.is_active) tag = ' ✅ Active';
      else if (mostRecent && s.id === mostRecent.id) tag = ' 🕐 Latest';
      opts.push({ value: String(s.id), label: `${s.name}${tag}` });
    });
    return opts;
  }, [stylesList]);

  const selectedStyle = stylesList.find((s) => s.id === selectedStyleId)
    || (selectedStyleId === null && activeStyle)
    || null;

  const projectId = selProject?.id ?? selCourse?.project_id;

  useEffect(() => {
    if (!courseId) return;
    dispatch(fetchCddsThunk(courseId));
    dispatch(fetchStylesThunk());
  }, [courseId, projectId, dispatch]);

  useEffect(() => {
    if (activeStyle?.id && selectedStyleId === null) {
      setSelectedStyleId(activeStyle.id);
    }
  }, [activeStyle, selectedStyleId]);

  useEffect(() => {
    if (activeCdd?.id) {
      setViewCddId(activeCdd.id);
      setSelectedCddId(activeCdd.id);
      dispatch(fetchCddVersionsThunk(activeCdd.id));
    }
  }, [activeCdd, dispatch]);

  useEffect(() => {
    if (displayCdd?.active_version) {
      setViewVersion(displayCdd.active_version);
    }
  }, [displayCdd?.id, displayCdd?.active_version]);

  useEffect(() => {
    if (!displayCdd?.id || !viewVersion) {
      setVersionDetail(null);
      return;
    }
    let cancelled = false;
    async function loadVer() {
      try {
        const ver = await cddService.getVersion(displayCdd.id, viewVersion);
        if (!cancelled) setVersionDetail(ver);
      } catch {
        if (!cancelled) {
          setVersionDetail(displayCdd.active_content || {
            full_content: displayCdd.active_content?.full_content
              || displayCdd.active_version?.full_content
              || '',
            sections: displayCdd.active_content?.sections,
          });
        }
      }
    }
    loadVer();
    return () => { cancelled = true; };
  }, [displayCdd, viewVersion]);

  useEffect(() => {
    async function loadSaved() {
      if (!selProject?.id) return;
      try {
        const list = await adminService.listInstructions({
          component: 'cdd',
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

  async function onGenerate() {
    const valid = await generateForm.trigger();
    if (!valid) return;
    const data = generateForm.getValues();
    const payload = {
      course_id: Number(courseId),
      project_id: selProject?.id,
      course_title: data.course_title,
      document_title: data.document_title,
      estimated_duration_hours: data.duration_hours || 8,
      extra_instructions: extraInstructions,
      style_id: selectedStyleId || null,
      model_choice: modelChoice,
      target_audience: targetAudience,
      expert_domain: expertDomain,
      audience_category: audienceCategory,
      system_prompt_override: promptConfig.systemPrompt || undefined,
      user_prompt_override: promptConfig.userPromptTemplate || undefined,
    };
    await dispatch(generateCddThunk(payload));
  }

  async function onSetActive(cddId) {
    await dispatch(setActiveCddThunk({ cddId, courseId: Number(courseId) }));
    setViewCddId(cddId);
  }

  async function onCommitVersion(data) {
    if (!selectedCddId || !displayCdd) return;
    const content = versionDetail?.full_content
      || displayCdd.active_content?.full_content
      || displayCdd.active_version?.full_content
      || '';
    const sections = versionDetail?.sections || displayCdd.active_content?.sections || {};
    await dispatch(commitCddVersionThunk({
      cddId: selectedCddId,
      data: { ...data, full_content: content, sections },
    }));
    setShowVersionModal(false);
    versionForm.reset();
    dispatch(fetchCddVersionsThunk(selectedCddId));
  }

  async function onSaveCddBlock({ blockKey, content, reason }) {
    if (!displayCdd?.id) return;
    setSavingBlock(true);
    try {
      const fullContent = versionDetail?.full_content
        || displayCdd.active_content?.full_content
        || '';
      const sectionsObj = versionDetail?.sections || displayCdd.active_content?.sections || {};
      const patched = patchCddBlock(fullContent, sectionsObj, blockKey, content);
      await dispatch(commitCddVersionThunk({
        cddId: displayCdd.id,
        data: {
          tag: `edit-${blockKey.replace(/\s+/g, '-').toLowerCase()}`,
          reason,
          full_content: patched.full_content,
          sections: patched.sections,
        },
      })).unwrap();
      dispatch(fetchCddVersionsThunk(displayCdd.id));
      dispatch(fetchCddsThunk(courseId));
    } finally {
      setSavingBlock(false);
    }
  }

  async function onExport(format) {
    if (!displayCdd?.id) return;
    const response = await dispatch(exportCddThunk({ cddId: displayCdd.id, format })).unwrap();
    downloadBlob(response.data, `cdd-${displayCdd.id}.${format}`);
  }

  function handleDownloadPrompt() {
    const md = buildPromptDownloadMd({
      projectName: selProject?.name,
      clusterName: selCluster?.name,
      courseName: selCourse?.name,
      component: 'cdd',
      promptName: 'cdd_generation',
      promptVersion: 'active',
      systemPrompt: promptConfig.systemPrompt,
      userPromptTemplate: promptConfig.userPromptTemplate,
      extraInstructions,
    });
    downloadBlob(new Blob([md], { type: 'application/msword' }), 'prompt_cdd_active.doc');
  }

  async function handleSaveInstructions() {
    if (!saveInstrName.trim() || !extraInstructions.trim()) return;
    await adminService.saveInstruction({
      name: saveInstrName.trim(),
      component: 'cdd',
      content: extraInstructions.trim(),
      project_id: selProject?.id,
      cluster_id: selCluster?.id,
      course_id: Number(courseId),
      as_new_version: saveInstrNewVer,
    });
    setShowSaveInstr(false);
    setSaveInstrName('');
    const list = await adminService.listInstructions({
      component: 'cdd',
      project_id: selProject?.id,
      course_id: Number(courseId),
    });
    setSavedInstrs(list || []);
  }

  const stylePinLabel = selectedStyle
    ? (selectedStyle.is_active ? 'active style' : 'manually selected')
    : null;

  const previewFullContent = versionDetail?.full_content
    || displayCdd?.active_content?.full_content
    || displayCdd?.active_version?.full_content
    || '';
  const previewSections = versionDetail?.sections || displayCdd?.active_content?.sections;

  return (
    <PageContainer
      title=""
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'CDD' }]}
      noPadding
    >
      <div className={styles.page}>
        <SectionBadge
          icon="📘"
          title="Course Design Document (CDD)"
          subtitle="Define learning objectives, tone, module structure, and quality standards. All downstream Blueprints and lessons inherit from this document automatically."
        />

        <div className={styles.layout}>
          {/* Left — Create New CDD */}
          <section className={styles.panel}>
            <h2 className={styles.panel__title}>➕ Create New CDD</h2>
            <p className={styles.requiredHint}>
              Fields marked <span className={styles.requiredMark}>*</span> are required.
            </p>

            {stylesList.length > 0 ? (
              <>
                <Select
                  label="🎨 Style for this CDD"
                  options={styleOptions}
                  value={selectedStyleId != null ? String(selectedStyleId) : ''}
                  onChange={(e) => setSelectedStyleId(e.target.value ? Number(e.target.value) : null)}
                />
                {selectedStyle ? (
                  <div className={styles.styleBannerOk}>
                    🎨 <strong>{selectedStyle.name}</strong> will be applied
                    <span className={styles.styleBannerOk__muted}> ({stylePinLabel})</span>
                  </div>
                ) : (
                  <div className={styles.styleBannerWarn}>
                    ⚠️ No style selected — CDD will be generated without style constraints.
                  </div>
                )}
              </>
            ) : (
              <div className={styles.styleBannerInfo}>
                ℹ️ No styles created yet. Go to the <strong>Style</strong> tab to create one.
              </div>
            )}

            <form className={styles.form} onSubmit={(e) => e.preventDefault()}>
              <Input
                label="Course Title *"
                required
                placeholder="e.g. Foundations of Clinical Nursing"
                error={generateForm.formState.errors.course_title?.message}
                {...generateForm.register('course_title')}
              />
              <Input
                label="Document Title"
                placeholder="e.g. Nursing Foundations CDD v1"
                {...generateForm.register('document_title')}
              />
              <Input
                label="Estimated Duration (hours) *"
                type="number"
                min={1}
                max={500}
                {...generateForm.register('duration_hours', { valueAsNumber: true })}
              />

              <div className={styles.extraSection}>
                <label className={styles.extraSection__label}>
                  💬 Additional Instructions <span className={styles.optional}>(optional)</span>
                </label>
                {savedInstrs.length > 0 && (
                  <div className={styles.instrLoad}>
                    <Select
                      label=""
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
                  className={styles.textarea}
                  rows={4}
                  placeholder="e.g. Focus on clinical simulation. Include DEI examples. Emphasise Bloom's levels 4–6."
                  value={extraInstructions}
                  onChange={(e) => setExtraInstructions(e.target.value)}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  fullWidth
                  onClick={() => setShowSaveInstr(!showSaveInstr)}
                >
                  💾 Save Instructions
                </Button>
                {showSaveInstr && (
                  <div className={styles.saveInstrPanel}>
                    <Input
                      placeholder="e.g. Clinical simulation focus"
                      value={saveInstrName}
                      onChange={(e) => setSaveInstrName(e.target.value)}
                    />
                    <label className={styles.checkLabel}>
                      <input
                        type="checkbox"
                        checked={saveInstrNewVer}
                        onChange={(e) => setSaveInstrNewVer(e.target.checked)}
                      />
                      New version
                    </label>
                    <Button variant="primary" size="sm" type="button" onClick={handleSaveInstructions}>
                      Confirm
                    </Button>
                  </div>
                )}
              </div>
            </form>

            {error && <ErrorState message={error} onRetry={() => dispatch(fetchCddsThunk(courseId))} />}
          </section>

          {/* Right — CDD Library */}
          <section className={styles.panel}>
            <h2 className={styles.panel__title}>📂 Your Course Design Documents</h2>

            {isLoading ? (
              <div className={styles.center}><Loader size="lg" /></div>
            ) : cdds.length === 0 ? (
              <EmptyState
                title="No CDDs yet"
                message="Create your first CDD using the form on the left."
              />
            ) : (
              <>
                <Select
                  label="Select CDD to View/Edit"
                  options={cdds.map((c) => ({
                    value: String(c.id),
                    label: `${c.title || c.course_title} (ID: ${c.id})`,
                  }))}
                  value={viewCddId != null ? String(viewCddId) : ''}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    setViewCddId(id);
                    setSelectedCddId(id);
                    if (id) dispatch(fetchCddVersionsThunk(id));
                  }}
                />

                {displayCdd && (
                  <>
                    <div className={styles.metrics}>
                      <div className={styles.metric}>
                        <span className={styles.metric__label}>Active Version</span>
                        <span className={styles.metric__value}>{displayCdd.active_version || '—'}</span>
                      </div>
                      <div className={styles.metric}>
                        <span className={styles.metric__label}>State</span>
                        <span className={styles.metric__value}>
                          {(displayCdd.workflow_state || 'draft').replace(/^\w/, (c) => c.toUpperCase())}
                        </span>
                      </div>
                      <div className={styles.metric}>
                        <span className={styles.metric__label}>Total Versions</span>
                        <span className={styles.metric__value}>{versions.length}</span>
                      </div>
                    </div>

                    <ul className={styles.list}>
                      {cdds.map((cdd) => (
                        <li
                          key={cdd.id}
                          className={`${styles.listItem} ${viewCddId === cdd.id ? styles['listItem--active'] : ''}`}
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

                    <div className={styles.activeContent}>
                      <div className={styles.activeContent__header}>
                        <h3 className={styles.activeContent__title}>
                          {displayCdd.title || displayCdd.course_title}
                        </h3>
                        <div className={styles.activeContent__actions}>
                          <Button variant="ghost" size="sm" onClick={() => onExport('markdown')}>↓ MD</Button>
                          <Button variant="ghost" size="sm" onClick={() => onExport('docx')}>↓ DOCX</Button>
                          <Button variant="ghost" size="sm" onClick={handleDownloadPrompt}>⬇️ Prompt</Button>
                          <Button variant="secondary" size="sm" onClick={() => setShowVersionModal(true)}>
                            + Save Version
                          </Button>
                        </div>
                      </div>

                      {versions.length > 0 && (
                        <div className={styles.versionRow}>
                          <Select
                            label="View Version"
                            options={versions.map((v) => ({
                              value: v.version,
                              label: `${v.version}${v.is_active ? ' (active)' : ''}`,
                            }))}
                            value={viewVersion || displayCdd.active_version || ''}
                            onChange={(e) => setViewVersion(e.target.value)}
                          />
                          {viewVersion && viewVersion !== displayCdd.active_version && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => dispatch(activateCddVersionThunk({
                                cddId: displayCdd.id,
                                version: viewVersion,
                                courseId: Number(courseId),
                              }))}
                            >
                              Set as Active Version
                            </Button>
                          )}
                        </div>
                      )}

                      {versions.length > 0 && (
                        <div className={styles.versions}>
                          <span className={styles.versions__label}>Versions:</span>
                          {versions.map((v) => (
                            <button
                              key={v.version}
                              type="button"
                              className={`${styles.versionTag} ${v.is_active ? styles['versionTag--active'] : ''}`}
                              title={v.is_active ? 'Active version' : `View ${v.version}`}
                              onClick={() => setViewVersion(v.version)}
                            >
                              {v.version}
                            </button>
                          ))}
                        </div>
                      )}

                      <CddContentView
                        fullContent={previewFullContent}
                        sections={previewSections}
                        editable
                        saving={savingBlock}
                        onSaveBlock={onSaveCddBlock}
                      />
                    </div>

                    <Button
                      variant="primary"
                      fullWidth
                      onClick={() => onSetActive(displayCdd.id)}
                      disabled={activeCdd?.id === displayCdd.id}
                    >
                      📌 Set as Active CDD for Generation
                    </Button>
                  </>
                )}
              </>
            )}
          </section>
        </div>

        <hr className={styles.divider} />

        <InlinePromptControls
          component="cdd"
          extraInstructions={extraInstructions}
          onPromptsChange={setPromptConfig}
          headerHint="📝 Fill in the course fields on the left, then configure the prompt and generate your CDD below."
        />

        <div className={styles.generateRow}>
          <Button
            variant="primary"
            size="lg"
            className={styles.generateRow__main}
            loading={isGenerating}
            onClick={onGenerate}
          >
            {isGenerating ? 'Generating CDD…' : '🤖 Generate CDD with AI'}
          </Button>
          <Button variant="secondary" size="lg" onClick={handleDownloadPrompt}>
            ⬇️ Download Prompt
          </Button>
        </div>
      </div>

      <Modal
        open={showVersionModal}
        onClose={() => setShowVersionModal(false)}
        title="Save New Version"
        size="sm"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setShowVersionModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={versionForm.handleSubmit(onCommitVersion)}>Save Version</Button>
          </>
        )}
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
