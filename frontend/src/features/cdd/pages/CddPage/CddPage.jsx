import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchCddsThunk, generateCddThunk, setActiveCddThunk,
  fetchCddVersionsThunk, commitCddVersionThunk, exportCddThunk,
  activateCddVersionThunk, regenerateCddItemThunk, regenerateCddSectionThunk,
  generateCddBlockThunk,
  resumeCddJobThunk,
  fetchArchivedCddsThunk, archiveCddThunk, restoreCddThunk, purgeCddThunk,
  bulkArchiveCddsThunk,
} from '@features/cdd/cddThunks';
import { cddService } from '@features/cdd/services/cddService';
import {
  selectCdds, selectActiveCdd, selectCddVersions,
  selectCddLoading, selectCddGenerating, selectCddError, selectCddBlockJob,
  selectArchivedCdds, selectCddArchiving, selectCddArchiveRefusal,
  resetBlockJob, clearArchiveRefusal,
} from '@features/cdd/cddSlice';
import { useAuth } from '@hooks/useAuth';
import BlockWidePanel from '@components/generation/BlockWidePanel/BlockWidePanel';
import DocumentArchivePanel from '@components/generation/DocumentArchivePanel/DocumentArchivePanel';
import {
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  selectModelChoice, selectExpertDomain, selectTargetAudience, selectAudienceCategory,
} from '@features/dashboard/dashboardSlice';
import { selectStyles, selectActiveStyle } from '@features/style/styleSlice';
import { fetchStylesThunk } from '@features/style/styleThunks';
import { adminService } from '@features/admin/services/adminService';
import sourceLibraryApi from '@features/sourceLibrary/services/sourceLibraryApi';
import { buildPromptDownloadMd } from '@utils/promptDefaults';
import { createCddSchema, commitVersionSchema } from '@utils/validation';
import { downloadBlob, formatDate } from '@utils/helpers';
import { inferBlockLabel } from '@utils/blockLabel';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import InlinePromptControls from '@components/generation/InlinePromptControls/InlinePromptControls';
import PromoteOverrideButton from '@components/generation/PromoteOverrideButton/PromoteOverrideButton';
import CddContentView from '@components/cdd/CddContentView/CddContentView';
import { patchCddBlock } from '@utils/cddContent';
import { replaceWorksheet } from '@utils/cddWorksheets';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import MultiSelect from '@components/common/MultiSelect/MultiSelect';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ErrorState from '@components/common/ErrorState/ErrorState';

import { useLabels } from '@hooks/useLabels';
import styles from './CddPage.module.scss';

const REF_DOCS_HINT = (L) => `All processed DIS Source Library documents are shown here. Whatever you select is retrieved and passed to the model as reference context when you click "Generate ${L.cdd} with AI".`;

function docLabel(doc) {
  return doc?.source_file_name || doc?.title || `Source ${doc?.job_id || doc?.document_id}`;
}

export default function CddPage() {
  const { courseId } = useParams();
  const dispatch = useAppDispatch();
  const L = useLabels();
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
  const blockJob = useAppSelector(selectCddBlockJob);
  const archivedCdds = useAppSelector(selectArchivedCdds);
  const isArchiving = useAppSelector(selectCddArchiving);
  const archiveRefusal = useAppSelector(selectCddArchiveRefusal);
  const { user } = useAuth();
  // Block-wide (digest-pipeline) generation is opt-in per DIS client — and the
  // client that matters is the COURSE'S, not the viewer's, because the pipeline
  // reads the course's own Source Library. Gating this on the user's own flag was
  // wrong: on 2026-08-13, 71 of 106 courses showed the panel and then failed the
  // POST with a 400. Courses now carry the same answer the endpoint gates on.
  //
  // `??`, not `||`: falling back only when the course has not loaded yet. Treating
  // a loaded `false` as "unknown" would put the wrong panel back on screen, and
  // treating an unloaded course as `false` would hide the panel from users who can
  // legitimately use it — the same false-negative that hid it after every login.
  const digestPipelineEnabled =
    selCourse?.digest_pipeline_enabled ?? Boolean(user?.digest_pipeline_enabled);

  const [selectedStyleId, setSelectedStyleId] = useState(null);
  const [refDocIds, setRefDocIds] = useState([]);
  const [sourceDocs, setSourceDocs] = useState([]);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState('');
  const [extraInstructions, setExtraInstructions] = useState('');
  const [promptConfig, setPromptConfig] = useState({ systemPrompt: '', userPromptTemplate: '', hasOverride: false });
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
  // Block-wide (digest-pipeline) generation inputs.
  const [blockLabel, setBlockLabel] = useState('');
  const [qualityTier, setQualityTier] = useState('standard');
  // True once the user types in the Block field, so the auto-prefill below stops
  // competing with them — including when they deliberately clear it (without this,
  // an empty field would look like "needs a prefill" and we'd refill it as they
  // delete). Reset per course, alongside blockLabel itself.
  const blockLabelTouched = useRef(false);
  const onBlockLabelChange = useCallback((value) => {
    blockLabelTouched.current = true;
    setBlockLabel(value);
  }, []);

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
    // Clear any block-job banner from a previously-viewed course so a stale
    // completed/failed status can't leak into this course's view.
    dispatch(resetBlockJob());
    setBlockLabel('');
    blockLabelTouched.current = false;
    dispatch(fetchCddsThunk(courseId));
    // Loaded on mount rather than on first expand: the count is shown on the
    // toggle itself, and a "Show archived" button that appears a second late
    // reads as the page still loading.
    dispatch(fetchArchivedCddsThunk(courseId));
    dispatch(fetchStylesThunk());
  }, [courseId, projectId, dispatch]);

  // Prefill the Block field from whatever text names the block, so the common case
  // (a block-specific prompt on a block-specific course) doesn't ask the user to
  // retype what's already on screen. Priority order = most specific first: the
  // selected prompt names the block it was written for ("a Block Blueprint for
  // Block 2 — Aircraft Drawings…"), which is a stronger signal than a course whose
  // name may be generic. The value is a real editable field value, not a
  // placeholder, so the user can see and correct what will be sent — the label is
  // an exact retrieval key server-side, so a silent guess would be unsafe.
  const inferredBlockLabel = useMemo(
    () => inferBlockLabel(
      promptConfig.systemPrompt,
      promptConfig.userPromptTemplate,
      displayCdd?.title,
      selCourse?.title,
      selCourse?.name,
    ),
    [promptConfig.systemPrompt, promptConfig.userPromptTemplate,
     displayCdd?.title, selCourse?.title, selCourse?.name],
  );

  useEffect(() => {
    if (!inferredBlockLabel || blockLabelTouched.current) return;
    setBlockLabel((current) => (current ? current : inferredBlockLabel));
  }, [inferredBlockLabel]);

  useEffect(() => {
    if (activeStyle?.id && selectedStyleId === null) {
      setSelectedStyleId(activeStyle.id);
    }
  }, [activeStyle, selectedStyleId]);

  // Reattach to a block-wide build already running server-side. The poll chain lives
  // only in browser memory, so a refresh (or a closed laptop, or the transient network
  // error that a 20-minute build reliably provokes) used to orphan the UI while the
  // job kept going — leaving a dead spinner, or tempting a re-submit that pays for a
  // second concurrent build. Runs on every mount and resolves to null when nothing is
  // in flight, so the common case costs one cheap request and changes no state.
  useEffect(() => {
    if (courseId) dispatch(resumeCddJobThunk({ courseId: Number(courseId) }));
    // projectId is in the deps because the resetBlockJob effect above lists it too:
    // projectId arrives asynchronously and commonly flips undefined→number just after
    // mount, which re-runs that effect and nulls blockJob while leaving isGenerating
    // true — a disabled button with no status line. Re-running here re-adopts the job.
    // The thunk bails out when a job is already being polled, so this cannot stack up
    // duplicate poll chains.
  }, [dispatch, courseId, projectId]);

  // Reference-document selector options. Same source as the Style tab: every
  // ingested Source Library document for this course's client (DIS `status` is a
  // review axis, not a processing flag, so filtering on it returns nothing).
  // Polls like the Style form so a document uploaded in another tab shows up.
  useEffect(() => {
    let cancelled = false;
    async function loadSourceDocs() {
      setSourceLoading(true);
      try {
        const params = {};
        if (selCourse?.id || courseId) params.course_id = selCourse?.id || courseId;
        else if (projectId) params.project_id = projectId;
        const res = await sourceLibraryApi.listDocuments(params);
        const list = res.documents || res.sources || [];
        if (!cancelled) {
          setSourceDocs(list);
          setSourceError('');
        }
      } catch {
        // Never block CDD generation on Source Library being reachable — the
        // selector just stays empty and generation falls back to the automatic
        // context retrieval it has always used.
        if (!cancelled) setSourceError('Source Library is unavailable right now. Upload or retry from Source Library.');
      } finally {
        if (!cancelled) setSourceLoading(false);
      }
    }
    loadSourceDocs();
    const interval = window.setInterval(loadSourceDocs, 30000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [selCourse?.id, projectId, courseId]);

  const refDocOptions = useMemo(
    () => (sourceDocs || []).map((d) => ({
      value: d.job_id || d.document_id,
      label: docLabel(d),
    })),
    [sourceDocs],
  );

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
      reference_document_ids: refDocIds,
      model_choice: modelChoice,
      target_audience: targetAudience,
      expert_domain: expertDomain,
      audience_category: audienceCategory,
      // Only forward as an override when the user explicitly applied one (e.g. via
      // "Use Now" on an AI suggestion) — the auto-loaded library default must never
      // be sent raw, since it bypasses all real CDD/style context-building server-side.
      system_prompt_override: promptConfig.hasOverride ? (promptConfig.systemPrompt || undefined) : undefined,
      user_prompt_override: promptConfig.hasOverride ? (promptConfig.userPromptTemplate || undefined) : undefined,
      // When the user picks a non-default library prompt (and hasn't applied an
      // ad-hoc override), send its id so that specific template drives generation
      // server-side — rendered through the normal variable/context path.
      prompt_id: (!promptConfig.hasOverride && !promptConfig.isDefault && promptConfig.selectedPromptId)
        ? promptConfig.selectedPromptId
        : undefined,
    };
    await dispatch(generateCddThunk(payload));
  }

  async function onGenerateBlock() {
    const data = generateForm.getValues();
    const payload = {
      block: blockLabel.trim(),
      course_id: Number(courseId),
      project_id: selProject?.id ?? projectId,
      course_title: data.course_title,
      document_title: data.document_title,
      estimated_duration_hours: data.duration_hours || undefined,
      quality_tier: qualityTier,
      extra_instructions: extraInstructions,
      model_choice: modelChoice,
      target_audience: targetAudience,
      expert_domain: expertDomain,
      // The style shown in the picker above, and announced by the "<style> will be
      // applied" banner. Omitting it made that banner false: block-wide generation
      // ran with no style at all while the page said otherwise. Sent as the id the
      // form displays rather than resolved server-side, so what is applied is always
      // what the user was shown.
      style_id: selectedStyleId || null,
      // The prompt shown in the "Prompt Template" dropdown above. Without this the
      // server can't reach its DB tier and falls through to the generic shipped
      // cdd_generation.md file — so the panel would silently distill its guidance
      // from a template the user never selected (verified: with the id, resolution
      // is db/v4/14443c; without it, file/v1/1439c).
      //
      // Sent whenever a prompt is selected, including when an ad-hoc override is
      // active: BlockWideGenerateRequest has no *_prompt_override fields, so the
      // override text cannot be transmitted on this path at all, and the selected
      // template is a far closer approximation than the generic default. An id that
      // doesn't resolve to a pipeline row is ignored server-side, so this is safe.
      prompt_id: promptConfig.selectedPromptId || undefined,
    };
    await dispatch(generateCddBlockThunk(payload));
  }

  async function onSetActive(cddId) {
    await dispatch(setActiveCddThunk({ cddId, courseId: Number(courseId) }));
    setViewCddId(cddId);
  }

  // ── Archive management ────────────────────────────────────────────────────
  // Every handler passes courseId so the shared thunks can refetch both the
  // live and archived lists — archiving moves a row between them.
  const cid = Number(courseId);

  function onArchiveCdd(cddId, { unpin } = {}) {
    // The selection follows the row out of the list, so the page does not keep
    // showing the contents of a document that is no longer in it.
    if (viewCddId === cddId) setViewCddId(null);
    dispatch(archiveCddThunk({ id: cddId, courseId: cid, unpin }));
  }

  function onRestoreCdd(cddId) {
    dispatch(restoreCddThunk({ id: cddId, courseId: cid }));
  }

  function onPurgeCdd(cddId) {
    dispatch(purgeCddThunk({ id: cddId, courseId: cid }));
  }

  function onBulkArchiveCdds(ids) {
    if (ids.includes(viewCddId)) setViewCddId(null);
    dispatch(bulkArchiveCddsThunk({ ids, courseId: cid, projectId }));
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

  // Version tags are stored in a String(20) column, so they must stay short.
  // We use a numeric "<prefix>-vN" label and keep the human-readable detail in
  // the change reason (which is unbounded text).
  function nextVersionTag(prefix) {
    const maxNum = (versions || []).reduce((max, v) => {
      const m = /(\d+)\s*$/.exec(String(v.version || ''));
      return m ? Math.max(max, Number(m[1])) : max;
    }, 0);
    const tag = `${prefix}-v${maxNum + 1}`;
    return tag.length <= 20 ? tag : `v${maxNum + 1}`;
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
          tag: nextVersionTag('edit'),
          reason: reason || `Edited ${blockKey}`,
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

  async function commitRegeneratedBlock(blockKey, newContent, reason) {
    const fullContent = versionDetail?.full_content
      || displayCdd.active_content?.full_content
      || '';
    const sectionsObj = versionDetail?.sections || displayCdd.active_content?.sections || {};
    const patched = patchCddBlock(fullContent, sectionsObj, blockKey, newContent);
    await dispatch(commitCddVersionThunk({
      cddId: displayCdd.id,
      data: {
        tag: nextVersionTag('regen'),
        reason,
        full_content: patched.full_content,
        sections: patched.sections,
      },
    })).unwrap();
    dispatch(fetchCddVersionsThunk(displayCdd.id));
    dispatch(fetchCddsThunk(courseId));
  }

  async function onRegenerateCddSection({ blockKey, instruction, dluWorksheetKey, dluCourseStructure }) {
    if (!displayCdd?.id) return;
    const res = await dispatch(regenerateCddSectionThunk({
      cddId: displayCdd.id,
      sectionKey: blockKey,
      feedback: instruction,
      modelChoice,
    })).unwrap();
    // The model returned the section untouched — a legitimate outcome when the
    // instruction cannot be satisfied from the context it was given. Committing
    // it would save a version identical to the current one and show a success
    // toast, which is why "I regenerated and nothing happened" was impossible to
    // tell apart from a broken feature. Say so instead, and save nothing.
    // The thunk has already reported this (see notifyRegenOutcome) — toasting
    // again here is what put a success and a failure on screen together.
    if (res?.changed === false) return;
    if (res?.updated_content != null) {
      const reason = instruction
        ? `AI regenerated ${blockKey}: ${instruction}`
        : `AI regenerated ${blockKey}`;
      if (dluWorksheetKey) {
        // DLU: splice the regenerated worksheet back into Course Structure.
        const newCs = replaceWorksheet(dluCourseStructure, dluWorksheetKey, res.updated_content);
        await commitRegeneratedBlock('Course Structure', newCs, reason);
      } else {
        await commitRegeneratedBlock(blockKey, res.updated_content, reason);
      }
    }
  }

  async function onRegenerateCddItem({
    blockKey, sectionContent, itemIndex, instruction, useSources,
    dluWorksheetKey, dluCourseStructure,
  }) {
    if (!displayCdd?.id) return;
    const res = await dispatch(regenerateCddItemThunk({
      cddId: displayCdd.id,
      sectionKey: blockKey,
      sectionContent,
      itemIndex,
      feedback: instruction,
      useSources,
      modelChoice,
    })).unwrap();
    // Same contract as the section path above: an item returned untouched must
    // not be committed as a new version. The thunk has already said so.
    if (res?.changed === false) return;
    if (res?.updated_content != null) {
      const reason = instruction
        ? `AI regenerated ${blockKey} item ${itemIndex + 1}: ${instruction}`
        : `AI regenerated ${blockKey} item ${itemIndex + 1}`;
      if (dluWorksheetKey) {
        // DLU: res.updated_content is the worksheet with the item patched —
        // splice it back into Course Structure before committing.
        const newCs = replaceWorksheet(dluCourseStructure, dluWorksheetKey, res.updated_content);
        await commitRegeneratedBlock('Course Structure', newCs, reason);
      } else {
        await commitRegeneratedBlock(blockKey, res.updated_content, reason);
      }
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
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: L.cdd }]}
      noPadding
    >
      <div className={styles.page}>
        <SectionBadge
          icon="📘"
          title={L.phrase('cdd', 'Title Design Document (CDD)')}
          subtitle={`Define learning objectives, tone, module structure, and quality standards. All downstream ${L.blueprints} and lessons inherit from this document automatically.`}
        />

        <div className={styles.layout}>
          <details className={styles.accordion} open>
            <summary className={styles.accordion__summary}>➕ Create New {L.cdd}</summary>
            <div className={styles.accordion__body}>
              <p className={styles.requiredHint}>
                Fields marked <span className={styles.requiredMark}>*</span> are required.
              </p>

              {stylesList.length > 0 ? (
                <>
                  <Select
                    label={`🎨 ${L.style} for this ${L.cdd}`}
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
                      ⚠️ No {L.styleLower} selected — {L.cdd} will be generated without {L.styleLower} constraints.
                    </div>
                  )}
                </>
              ) : (
                <div className={styles.styleBannerInfo}>
                  ℹ️ No {L.stylesLower} created yet. Go to the <strong>{L.style}</strong> tab to create one.
                </div>
              )}

              <div className={styles.sectionLabel}>📎 CDD Reference Documents</div>

              <div className={styles.fieldRow}>
                <label className={styles.fieldLabel} htmlFor="cdd-ref-docs">
                  Select processed Source Library documents
                  <span className={styles.helpIcon} title={REF_DOCS_HINT(L)} aria-label={REF_DOCS_HINT(L)}>?</span>
                </label>
                <MultiSelect
                  placeholder={sourceLoading && !refDocOptions.length
                    ? 'Loading Source Library documents…'
                    : 'Choose reference documents'}
                  options={refDocOptions}
                  value={refDocIds}
                  onChange={setRefDocIds}
                  disabled={!refDocOptions.length}
                  hint={sourceError || (!refDocOptions.length && !sourceLoading
                    ? 'No processed documents found. Upload them in Source Library first.'
                    : undefined)}
                />
                {refDocIds.length > 0 && (
                  <div className={styles.refDocsBanner}>
                    📎 <strong>{refDocIds.length}</strong> document{refDocIds.length === 1 ? '' : 's'} will be
                    passed as reference context to this {L.cdd} generation.
                  </div>
                )}
              </div>

              <form className={styles.form} onSubmit={(e) => e.preventDefault()}>
                <Input
                  label={`${L.title} *`}
                  required
                  placeholder="e.g. Foundations of Clinical Nursing"
                  error={generateForm.formState.errors.course_title?.message}
                  {...generateForm.register('course_title')}
                />
                <Input
                  label="Document Title"
                  placeholder={`e.g. Nursing Foundations ${L.cdd} v1`}
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
                    className={styles.saveInstrBtn}
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
            </div>
          </details>

          <details className={styles.accordion}>
            <summary className={styles.accordion__summary}>📂 Your Title Design Documents</summary>
            <div className={styles.accordion__body}>
              {isLoading ? (
                <div className={styles.center}><Loader size="lg" /></div>
              ) : (cdds.length === 0 && archivedCdds.length === 0) ? (
                <EmptyState
                  title="No CDDs yet"
                  message={`Create your first ${L.cdd} using the form above.`}
                />
              ) : (
                <>
                  {/* Rendered whenever there is anything to show, live OR archived,
                      and outside the `displayCdd` guard below. Archiving the last
                      live CDD would otherwise take the archived list off screen
                      with it, leaving no way to restore what was just archived. */}
                  <DocumentArchivePanel
                    label={L.cdd}
                    docs={cdds}
                    archivedDocs={archivedCdds}
                    activeId={activeCdd?.id ?? null}
                    selectedId={viewCddId}
                    busy={isArchiving}
                    canPurge={user?.role === 'admin'}
                    onSelect={(id) => {
                      setViewCddId(id);
                      setSelectedCddId(id);
                      dispatch(fetchCddVersionsThunk(id));
                    }}
                    onSetActive={onSetActive}
                    onArchive={onArchiveCdd}
                    onRestore={onRestoreCdd}
                    onPurge={onPurgeCdd}
                    onBulkArchive={onBulkArchiveCdds}
                    refusal={archiveRefusal}
                    onDismissRefusal={() => dispatch(clearArchiveRefusal())}
                  />

                  {cdds.length > 0 && (
                  <Select
                    label={`Select ${L.cdd} to View/Edit`}
                    // Title alone does not identify a row — a course can hold
                    // dozens whose titles are character-for-character identical.
                    // The date and author are what make the option pickable.
                    options={cdds.map((c) => ({
                      value: String(c.id),
                      label: [
                        `${c.title || c.course_title} (ID: ${c.id})`,
                        formatDate(c.created_at),
                        c.created_by,
                      ].filter(Boolean).join(' — '),
                    }))}
                    value={viewCddId != null ? String(viewCddId) : ''}
                    onChange={(e) => {
                      const id = Number(e.target.value);
                      setViewCddId(id);
                      setSelectedCddId(id);
                      if (id) dispatch(fetchCddVersionsThunk(id));
                    }}
                  />
                  )}

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

                      <div className={styles.activeContent}>
                        <div className={styles.activeContent__header}>
                          <h3 className={styles.activeContent__title}>
                            {displayCdd.title || displayCdd.course_title}
                          </h3>
                          <div className={styles.activeContent__actions}>
                            <Button variant="ghost" size="sm" onClick={() => onExport('md')}>↓ MD</Button>
                            <Button variant="ghost" size="sm" onClick={() => onExport('docx')}>↓ DOCX</Button>
                            <Button variant="ghost" size="sm" onClick={() => onExport('xlsx')}>↓ XLSX</Button>
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

                        <PromoteOverrideButton
                          sourceType="cdd"
                          artifactId={displayCdd?.id}
                          version={versionDetail?.version || displayCdd?.active_content?.version}
                          generationParams={versionDetail?.generation_params
                            || displayCdd?.active_content?.generation_params}
                        />
                        <CddContentView
                          fullContent={previewFullContent}
                          sections={previewSections}
                          editable
                          saving={savingBlock}
                          onSaveBlock={onSaveCddBlock}
                          onRegenerateSection={onRegenerateCddSection}
                          onRegenerateItem={onRegenerateCddItem}
                        />
                      </div>

                      <Button
                        variant="primary"
                        className={styles.setActiveBtn}
                        onClick={() => onSetActive(displayCdd.id)}
                        disabled={activeCdd?.id === displayCdd.id}
                      >
                        📌 Set as Active {L.cdd} for Generation
                      </Button>
                    </>
                  )}
                </>
              )}
            </div>
          </details>

          <details className={styles.accordion}>
            <summary className={styles.accordion__summary}>🎯 CDD Prompts</summary>
            <div className={styles.accordion__body}>
              <InlinePromptControls
                component="cdd"
                embedded
                extraInstructions={extraInstructions}
                onPromptsChange={setPromptConfig}
                headerHint={`📝 Fill in the ${L.titleLower} fields above, then configure the prompt and generate your ${L.cdd} below.`}
              />

              <div className={styles.generateRow}>
                <Button
                  variant="primary"
                  size="md"
                  className={styles.generateRow__main}
                  loading={isGenerating}
                  onClick={onGenerate}
                >
                  {isGenerating ? `Generating ${L.cdd}…` : `🤖 Generate ${L.cdd} with AI`}
                </Button>
                <Button variant="secondary" size="md" onClick={handleDownloadPrompt}>
                  ⬇️ Download Prompt
                </Button>
              </div>

              {digestPipelineEnabled && (
                <BlockWidePanel
                  label={L.cdd}
                  hint={`Generate a whole-block ${L.cdd} from every source in the block — enumerated day-by-day, digested, then reduced with coverage checks. Applies the ${L.styleLower}, additional instructions and duration set above; the document selection does not apply, since every ingested source in the block is used. Runs in the background; the ${L.cdd} is pinned as active when it finishes.`}
                  block={blockLabel}
                  onBlockChange={onBlockLabelChange}
                  qualityTier={qualityTier}
                  onQualityTierChange={setQualityTier}
                  isGenerating={isGenerating}
                  blockJob={blockJob}
                  onGenerate={onGenerateBlock}
                />
              )}
            </div>
          </details>
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
