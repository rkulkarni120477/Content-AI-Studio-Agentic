import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchBlueprintsThunk, generateBlueprintThunk, setActiveBlueprintThunk,
  fetchBlueprintVersionsThunk, commitBlueprintVersionThunk, exportBlueprintThunk,
  activateBlueprintVersionThunk, regenerateBlueprintItemThunk, regenerateBlueprintSectionThunk,
  generateBlueprintBlockThunk,
  resumeBlueprintJobThunk,
  fetchArchivedBlueprintsThunk, archiveBlueprintThunk, restoreBlueprintThunk,
  purgeBlueprintThunk, bulkArchiveBlueprintsThunk,
} from '@features/blueprint/blueprintThunks';
import { blueprintService } from '@features/blueprint/services/blueprintService';
import {
  selectBlueprints, selectActiveBlueprint, selectBlueprintVersions,
  selectBlueprintLoading, selectBlueprintGenerating, selectBlueprintError,
  selectBlueprintGenerationMode, setGenerationMode, selectBlueprintBlockJob, resetBlockJob,
  selectArchivedBlueprints, selectBlueprintArchiving, selectBlueprintArchiveRefusal,
  clearArchiveRefusal,
} from '@features/blueprint/blueprintSlice';
import { selectActiveCdd, selectCdds } from '@features/cdd/cddSlice';
import { fetchCddsThunk } from '@features/cdd/cddThunks';
import { cddService } from '@features/cdd/services/cddService';
import {
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  selectModelChoice,
} from '@features/dashboard/dashboardSlice';
import { selectActiveStyle } from '@features/style/styleSlice';
import { fetchStylesThunk } from '@features/style/styleThunks';
import { selectIsAdmin } from '@features/auth/authSlice';
import { useAuth } from '@hooks/useAuth';
import DocumentArchivePanel from '@components/generation/DocumentArchivePanel/DocumentArchivePanel';
import BlockWidePanel from '@components/generation/BlockWidePanel/BlockWidePanel';
import { adminService } from '@features/admin/services/adminService';
import {
  buildModuleOptions,
  buildDayOptions,
  detectDluCdd,
  existingModuleNumbersForCdd,
  buildExtraInstructionsBlock,
} from '@utils/blueprintModules';
import { parseSectionsFromText } from '@utils/blueprintContent';
import { detectDluBlueprint, replaceDluBlueprintSection } from '@utils/dluBlueprint';
import { buildPromptDownloadMd } from '@utils/promptDefaults';
import { commitVersionSchema } from '@utils/validation';
import { GENERATION_MODES } from '@utils/constants';
import { downloadBlob, formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import InlinePromptControls from '@components/generation/InlinePromptControls/InlinePromptControls';
import PromoteOverrideButton from '@components/generation/PromoteOverrideButton/PromoteOverrideButton';
import BlueprintContentView from '@components/blueprint/BlueprintContentView/BlueprintContentView';
import toast from 'react-hot-toast';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ErrorState from '@components/common/ErrorState/ErrorState';

import { inferBlockLabel } from '@utils/blockLabel';
import { useLabels } from '@hooks/useLabels';
import styles from './BlueprintPage.module.scss';

const NONE_CDD = '';

export default function BlueprintPage() {
  const { courseId } = useParams();
  const dispatch = useAppDispatch();
  const L = useLabels();
  const blueprints = useAppSelector(selectBlueprints);
  const activeBlueprint = useAppSelector(selectActiveBlueprint);
  const versions = useAppSelector(selectBlueprintVersions);
  const cdds = useAppSelector(selectCdds);
  const activeCdd = useAppSelector(selectActiveCdd);
  const activeStyle = useAppSelector(selectActiveStyle);
  const selProject = useAppSelector(selectSelectedProject);
  const selCluster = useAppSelector(selectSelectedCluster);
  const selCourse = useAppSelector(selectSelectedCourse);
  const modelChoice = useAppSelector(selectModelChoice);
  const genMode = useAppSelector(selectBlueprintGenerationMode);
  const isAdmin = useAppSelector(selectIsAdmin);
  const isLoading = useAppSelector(selectBlueprintLoading);
  const isGenerating = useAppSelector(selectBlueprintGenerating);
  const error = useAppSelector(selectBlueprintError);
  const blockJob = useAppSelector(selectBlueprintBlockJob);
  const archivedBlueprints = useAppSelector(selectArchivedBlueprints);
  const isArchiving = useAppSelector(selectBlueprintArchiving);
  const archiveRefusal = useAppSelector(selectBlueprintArchiveRefusal);
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

  const [linkedCddId, setLinkedCddId] = useState(null);
  const [cddContent, setCddContent] = useState('');
  // Block-wide (digest-pipeline) generation inputs.
  const [blockLabel, setBlockLabel] = useState('');
  const [qualityTier, setQualityTier] = useState('standard');
  // See CddPage: once the user edits Block, stop auto-prefilling it — otherwise
  // clearing the field would read as "needs a prefill" and refill as they delete.
  const blockLabelTouched = useRef(false);
  const onBlockLabelChange = useCallback((value) => {
    blockLabelTouched.current = true;
    setBlockLabel(value);
  }, []);
  const [moduleSelKey, setModuleSelKey] = useState('');
  const [documentTitle, setDocumentTitle] = useState('');
  const [extraInstructions, setExtraInstructions] = useState('');
  const [promptConfig, setPromptConfig] = useState({ systemPrompt: '', userPromptTemplate: '', hasOverride: false });
  const [savedInstrs, setSavedInstrs] = useState([]);
  const [loadInstrSel, setLoadInstrSel] = useState('— Start fresh —');
  const [showSaveInstr, setShowSaveInstr] = useState(false);
  const [saveInstrName, setSaveInstrName] = useState('');
  const [saveInstrNewVer, setSaveInstrNewVer] = useState(false);
  const [viewBpId, setViewBpId] = useState(null);
  const [viewBpDetail, setViewBpDetail] = useState(null);
  const [selectedBpId, setSelectedBpId] = useState(null);
  const [viewVersion, setViewVersion] = useState(null);
  const [versionDetail, setVersionDetail] = useState(null);
  const [moduleConfirmed, setModuleConfirmed] = useState(false);
  const [showSaveVersion, setShowSaveVersion] = useState(false);
  const [savingSection, setSavingSection] = useState(false);

  const versionForm = useForm({ resolver: zodResolver(commitVersionSchema) });

  const displayBp = viewBpDetail
    || (viewBpId
      ? (blueprints.find((b) => b.id === viewBpId) || (activeBlueprint?.id === viewBpId ? activeBlueprint : null))
      : activeBlueprint);

  const cddOptions = useMemo(() => {
    const opts = [{ value: NONE_CDD, label: '— None (standalone) —' }];
    cdds.forEach((c) => {
      opts.push({
        value: String(c.id),
        label: `${c.title || c.course_title} (${c.active_version || 'v1'})`,
      });
    });
    return opts;
  }, [cdds]);

  const existingNums = useMemo(
    () => existingModuleNumbersForCdd(blueprints, linkedCddId),
    [blueprints, linkedCddId],
  );

  // DLU/day-based CDDs list Days instead of Modules; standard CDDs are unchanged.
  const isDluCdd = useMemo(() => detectDluCdd(cddContent), [cddContent]);

  const moduleOptions = useMemo(
    () => (isDluCdd
      ? buildDayOptions(cddContent, existingNums)
      : buildModuleOptions(cddContent, existingNums)),
    [isDluCdd, cddContent, existingNums],
  );

  const selectedModuleOpt = moduleOptions.find(
    (o) => String(o.key) === String(moduleSelKey),
  ) || moduleOptions[0];

  const linkedCdd = linkedCddId
    ? cdds.find((c) => c.id === linkedCddId)
    : null;

  const projectId = selProject?.id ?? selCourse?.project_id;

  useEffect(() => {
    if (!courseId) return;
    // Clear any stale block-job banner from a previously-viewed course.
    dispatch(resetBlockJob());
    setBlockLabel('');
    blockLabelTouched.current = false;
    dispatch(fetchBlueprintsThunk(courseId));
    // Loaded on mount rather than on first expand: the count is shown on the
    // toggle itself, so it has to be known before the toggle is rendered.
    dispatch(fetchArchivedBlueprintsThunk(courseId));
    dispatch(fetchCddsThunk(courseId));
    dispatch(fetchStylesThunk());
  }, [courseId, projectId, dispatch]);

  // Prefill Block from the text that names it, most specific source first: the
  // selected prompt (written for a given block), then the linked CDD, then the
  // course. Editable — the user sees and can correct what gets sent, because the
  // label is an exact retrieval key server-side.
  const inferredBlockLabel = useMemo(
    () => inferBlockLabel(
      promptConfig.systemPrompt,
      promptConfig.userPromptTemplate,
      linkedCdd?.title,
      selCourse?.title,
      selCourse?.name,
    ),
    [promptConfig.systemPrompt, promptConfig.userPromptTemplate,
     linkedCdd?.title, selCourse?.title, selCourse?.name],
  );

  useEffect(() => {
    if (!inferredBlockLabel || blockLabelTouched.current) return;
    setBlockLabel((current) => (current ? current : inferredBlockLabel));
  }, [inferredBlockLabel]);

  useEffect(() => {
    if (activeCdd?.id && linkedCddId === null) {
      setLinkedCddId(activeCdd.id);
    }
  }, [activeCdd, linkedCddId]);

  // Reattach to a block-wide build already running server-side — see the identical
  // effect in CddPage. Declared after the resetBlockJob effect above so a course
  // switch clears the previous course's banner before this adopts the new one's job.
  useEffect(() => {
    if (courseId) dispatch(resumeBlueprintJobThunk({ courseId: Number(courseId) }));
    // projectId is in the deps because the resetBlockJob effect above lists it too:
    // projectId arrives asynchronously and commonly flips undefined→number just after
    // mount, which re-runs that effect and nulls blockJob while leaving isGenerating
    // true — a disabled button with no status line. Re-running here re-adopts the job.
    // The thunk bails out when a job is already being polled, so this cannot stack up
    // duplicate poll chains.
  }, [dispatch, courseId, projectId]);

  useEffect(() => {
    if (activeBlueprint?.id) {
      setViewBpId(activeBlueprint.id);
      setSelectedBpId(activeBlueprint.id);
      setViewBpDetail(activeBlueprint);
      dispatch(fetchBlueprintVersionsThunk(activeBlueprint.id));
    }
  }, [activeBlueprint, dispatch]);

  useEffect(() => {
    if (activeBlueprint?.id && viewBpId === activeBlueprint.id) {
      setViewBpDetail(activeBlueprint);
    }
  }, [activeBlueprint, viewBpId]);

  useEffect(() => {
    if (!viewBpId) {
      setViewBpDetail(null);
      return;
    }
    let cancelled = false;
    blueprintService.getBlueprint(viewBpId).then((bp) => {
      if (!cancelled) setViewBpDetail(bp);
    }).catch(() => {
      if (!cancelled) {
        setViewBpDetail(
          blueprints.find((b) => b.id === viewBpId)
          || (activeBlueprint?.id === viewBpId ? activeBlueprint : null),
        );
      }
    });
    return () => { cancelled = true; };
  }, [viewBpId, blueprints, activeBlueprint]);

  useEffect(() => {
    let cancelled = false;
    async function loadCddContent() {
      if (!linkedCddId) {
        setCddContent('');
        return;
      }
      try {
        const detail = await cddService.getCdd(linkedCddId);
        const content = detail?.active_content?.full_content
          || detail?.active_version?.full_content
          || '';
        if (!cancelled) setCddContent(content);
      } catch {
        const local = cdds.find((c) => c.id === linkedCddId);
        const content = local?.active_content?.full_content
          || local?.active_version?.full_content
          || '';
        if (!cancelled) setCddContent(content);
      }
    }
    loadCddContent();
    return () => { cancelled = true; };
  }, [linkedCddId, cdds]);

  useEffect(() => {
    if (moduleOptions.length > 0 && !moduleSelKey) {
      setModuleSelKey(String(moduleOptions[0].key));
    }
  }, [moduleOptions, moduleSelKey]);

  useEffect(() => {
    async function loadSaved() {
      if (!selProject?.id) return;
      try {
        const list = await adminService.listInstructions({
          component: 'blueprint',
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

  useEffect(() => {
    if (!displayBp?.id || !viewVersion) {
      setVersionDetail(null);
      return;
    }
    let cancelled = false;
    async function loadVer() {
      try {
        const ver = await blueprintService.getVersion(displayBp.id, viewVersion);
        if (!cancelled) setVersionDetail(ver);
      } catch {
        if (!cancelled) {
          setVersionDetail(displayBp.active_content || {
            full_content: displayBp.active_content?.full_content || '',
            sections: displayBp.active_content?.sections,
          });
        }
      }
    }
    loadVer();
    return () => { cancelled = true; };
  }, [displayBp, viewVersion]);

  useEffect(() => {
    if (displayBp?.active_version) {
      setViewVersion(displayBp.active_version);
    }
  }, [displayBp?.id, displayBp?.active_version]);

  function onConfirmModule() {
    if (!selectedModuleOpt) {
      toast.error(isDluCdd ? 'Select a day first.' : 'Select a module or title-end item first.');
      return;
    }
    setModuleConfirmed(true);
    toast.success(isDluCdd ? 'Day selection confirmed.' : 'Module selection confirmed.');
  }

  async function onGenerate() {
    if (!selProject?.id) return;
    const mod = selectedModuleOpt;
    if (!mod) return;
    if (!linkedCddId && !cddContent) {
      toast.error(`Link a ${L.cdd} for best results, or continue with standalone generation.`);
    }

    const moduleRef = mod.isCourseEnd
      ? mod.courseEndLabel
      : (mod.label || `Module ${mod.key}`).replace(/\s*✓\s*$/, '').trim();

    const extraBlock = buildExtraInstructionsBlock({
      isCourseEnd: mod.isCourseEnd,
      courseEndLabel: mod.courseEndLabel,
      moduleNum: mod.key,
      isDay: mod.isDay,
      dayTitle: mod.title,
      extraInstructions,
    });

    let titleHint = '';
    if (documentTitle.trim()) {
      titleHint = `Preferred blueprint title: ${documentTitle.trim()}\n\n`;
    }

    const payload = {
      course_id: Number(courseId),
      project_id: selProject.id,
      cdd_id: linkedCddId || null,
      selected_module: moduleRef,
      day_number: mod.isDay ? mod.key : undefined,
      is_course_end: Boolean(mod.isCourseEnd),
      extra_instructions: titleHint + extraBlock,
      style_id: activeStyle?.id || null,
      model_choice: modelChoice,
      teacher_mode: genMode === GENERATION_MODES.TEACHER,
      // Only forward as an override when the user explicitly applied one (e.g. via
      // "Use Now" on an AI suggestion) — the auto-loaded library default must never
      // be sent raw, since it bypasses all real CDD/style context-building server-side.
      system_prompt_override: promptConfig.hasOverride ? (promptConfig.systemPrompt || undefined) : undefined,
      user_prompt_override: promptConfig.hasOverride ? (promptConfig.userPromptTemplate || undefined) : undefined,
      // Non-default library prompt selection drives generation server-side.
      prompt_id: (!promptConfig.hasOverride && !promptConfig.isDefault && promptConfig.selectedPromptId)
        ? promptConfig.selectedPromptId
        : undefined,
    };
    try {
      const bp = await dispatch(generateBlueprintThunk(payload)).unwrap();
      if (bp?.id) {
        setViewBpId(bp.id);
        setSelectedBpId(bp.id);
        setViewBpDetail(bp);
        dispatch(fetchBlueprintVersionsThunk(bp.id));
        await dispatch(setActiveBlueprintThunk({ blueprintId: bp.id, courseId: Number(courseId) }));
      }
      dispatch(fetchBlueprintsThunk(courseId));
      setModuleConfirmed(false);
    } catch {
      /* error surfaced via slice */
    }
  }

  async function onGenerateBlock() {
    const payload = {
      block: blockLabel.trim(),
      course_id: Number(courseId),
      project_id: selProject?.id ?? projectId,
      course_title: selCourse?.title || selCourse?.name || '',
      document_title: documentTitle.trim() || undefined,
      quality_tier: qualityTier,
      cdd_id: linkedCddId || undefined,
      extra_instructions: extraInstructions,
      model_choice: modelChoice,
      // Same active style the per-module path applies (line ~366). Without it a
      // block-wide Blueprint was the only generation in the app that ignored the
      // workspace's style entirely.
      style_id: activeStyle?.id || null,
      // See the matching comment in CddPage.onGenerateBlock: without this the server
      // cannot reach its DB prompt tier and distills guidance from the generic
      // shipped template instead of the one selected in the dropdown.
      prompt_id: promptConfig.selectedPromptId || undefined,
    };
    await dispatch(generateBlueprintBlockThunk(payload));
  }

  async function onPin(bpId) {
    await dispatch(setActiveBlueprintThunk({ blueprintId: bpId, courseId: Number(courseId) }));
    setViewBpId(bpId);
  }

  // ── Archive management ────────────────────────────────────────────────────
  // Every handler passes courseId so the shared thunks can refetch both the
  // live and archived lists — archiving moves a row between them.
  const bpCourseId = Number(courseId);

  function onArchiveBlueprint(bpId, { unpin } = {}) {
    // The selection follows the row out of the list, so the page does not keep
    // showing the contents of a document that is no longer in it.
    if (viewBpId === bpId) setViewBpId(null);
    dispatch(archiveBlueprintThunk({ id: bpId, courseId: bpCourseId, unpin }));
  }

  function onRestoreBlueprint(bpId) {
    dispatch(restoreBlueprintThunk({ id: bpId, courseId: bpCourseId }));
  }

  function onPurgeBlueprint(bpId) {
    dispatch(purgeBlueprintThunk({ id: bpId, courseId: bpCourseId }));
  }

  function onBulkArchiveBlueprints(ids) {
    if (ids.includes(viewBpId)) setViewBpId(null);
    dispatch(bulkArchiveBlueprintsThunk({ ids, courseId: bpCourseId, projectId }));
  }

  async function onCommitVersion(data) {
    if (!selectedBpId || !displayBp) return;
    const content = versionDetail?.full_content
      || displayBp.active_content?.full_content
      || '';
    await dispatch(commitBlueprintVersionThunk({
      blueprintId: selectedBpId,
      data: {
        ...data,
        full_content: content,
        sections: versionDetail?.sections || {},
      },
    }));
    setShowSaveVersion(false);
    versionForm.reset();
    dispatch(fetchBlueprintVersionsThunk(selectedBpId));
  }

  // Version tags live in a String(20) column, so keep them short ("<prefix>-vN")
  // and put the descriptive detail in the (longer) change reason.
  function nextBpVersionTag(prefix) {
    const maxNum = (versions || []).reduce((max, v) => {
      const m = /(\d+)\s*$/.exec(String(v.version || ''));
      return m ? Math.max(max, Number(m[1])) : max;
    }, 0);
    const tag = `${prefix}-v${maxNum + 1}`;
    return tag.length <= 20 ? tag : `v${maxNum + 1}`;
  }

  function patchBlueprintSection(sectionTitle, newContent) {
    const fullContent = versionDetail?.full_content
      || displayBp.active_content?.full_content
      || '';
    // DLU (day-based) blueprints: splice the edited/regenerated part back into the
    // numbered DLU structure instead of rebuilding as `## ` sections (which would
    // flatten and renumber the day blueprint). Standard blueprints skip this.
    if (detectDluBlueprint(fullContent)) {
      return {
        updatedSections: {},
        newFull: replaceDluBlueprintSection(fullContent, sectionTitle, newContent),
      };
    }
    let sections = versionDetail?.sections;
    if (!sections || typeof sections !== 'object' || !Object.keys(sections).length) {
      sections = parseSectionsFromText(fullContent);
    }
    const updatedSections = { ...sections, [sectionTitle]: newContent };
    const newFull = Object.entries(updatedSections)
      .filter(([, body]) => body?.trim())
      .map(([title, body]) => `## ${title}\n${body.trim()}`)
      .join('\n\n');
    return { updatedSections, newFull };
  }

  async function commitBlueprintSection(sectionTitle, newContent, reason, tagPrefix) {
    const { updatedSections, newFull } = patchBlueprintSection(sectionTitle, newContent);
    await dispatch(commitBlueprintVersionThunk({
      blueprintId: displayBp.id,
      data: {
        tag: nextBpVersionTag(tagPrefix),
        reason,
        full_content: newFull,
        sections: updatedSections,
      },
    })).unwrap();
    dispatch(fetchBlueprintVersionsThunk(displayBp.id));
    dispatch(fetchBlueprintsThunk(courseId));
  }

  async function onSaveBlueprintSection({ sectionTitle, content, reason }) {
    if (!displayBp?.id) return;
    setSavingSection(true);
    try {
      await commitBlueprintSection(sectionTitle, content, reason || `Edited ${sectionTitle}`, 'edit');
    } finally {
      setSavingSection(false);
    }
  }

  async function onRegenerateBlueprintSection({ sectionTitle, instruction }) {
    if (!displayBp?.id) return;
    const res = await dispatch(regenerateBlueprintSectionThunk({
      blueprintId: displayBp.id,
      sectionKey: sectionTitle,
      feedback: instruction,
      modelChoice,
      teacherMode: viewMode === 'teacher',
    })).unwrap();
    if (res?.updated_content != null) {
      const reason = instruction
        ? `AI regenerated ${sectionTitle}: ${instruction}`
        : `AI regenerated ${sectionTitle}`;
      await commitBlueprintSection(sectionTitle, res.updated_content, reason, 'regen');
    }
  }

  async function onRegenerateBlueprintItem({ sectionTitle, sectionContent, itemIndex, instruction }) {
    if (!displayBp?.id) return;
    const res = await dispatch(regenerateBlueprintItemThunk({
      blueprintId: displayBp.id,
      sectionKey: sectionTitle,
      sectionContent,
      itemIndex,
      feedback: instruction,
      modelChoice,
    })).unwrap();
    if (res?.updated_content != null) {
      const reason = instruction
        ? `AI regenerated ${sectionTitle} item ${itemIndex + 1}: ${instruction}`
        : `AI regenerated ${sectionTitle} item ${itemIndex + 1}`;
      await commitBlueprintSection(sectionTitle, res.updated_content, reason, 'regen');
    }
  }

  async function onExport(format) {
    if (!displayBp?.id) return;
    const response = await dispatch(exportBlueprintThunk({ blueprintId: displayBp.id, format })).unwrap();
    downloadBlob(response.data, `blueprint-${displayBp.id}.${format === 'docx' ? 'docx' : format}`);
  }

  function handleDownloadPrompt() {
    const mod = selectedModuleOpt;
    const moduleRef = mod?.isCourseEnd
      ? mod.courseEndLabel
      : (mod?.label || `Module ${mod?.key ?? 1}`).replace(/\s*✓\s*$/, '').trim();
    const extraBlock = buildExtraInstructionsBlock({
      isCourseEnd: mod?.isCourseEnd,
      courseEndLabel: mod?.courseEndLabel,
      moduleNum: mod?.key ?? 1,
      isDay: mod?.isDay,
      dayTitle: mod?.title,
      extraInstructions,
    });
    const md = buildPromptDownloadMd({
      projectName: selProject?.name,
      clusterName: selCluster?.name,
      courseName: selCourse?.name,
      component: 'blueprint',
      promptName: 'blueprint_generation',
      promptVersion: 'active',
      systemPrompt: promptConfig.systemPrompt,
      userPromptTemplate: promptConfig.userPromptTemplate,
      extraInstructions: extraBlock,
    });
    downloadBlob(new Blob([md], { type: 'application/msword' }), 'prompt_blueprint_active.doc');
  }

  async function handleSaveInstructions() {
    if (!saveInstrName.trim() || !extraInstructions.trim()) return;
    await adminService.saveInstruction({
      name: saveInstrName.trim(),
      component: 'blueprint',
      content: extraInstructions.trim(),
      project_id: selProject?.id,
      cluster_id: selCluster?.id,
      course_id: Number(courseId),
      as_new_version: saveInstrNewVer,
    });
    setShowSaveInstr(false);
    setSaveInstrName('');
    const list = await adminService.listInstructions({
      component: 'blueprint',
      project_id: selProject?.id,
      course_id: Number(courseId),
    });
    setSavedInstrs(list || []);
  }

  const styleLabel = activeStyle?.name || 'None — no active style';
  const styleOk = Boolean(activeStyle);
  const cddLabel = activeCdd
    ? `${activeCdd.title || activeCdd.course_title}`
  : `None — no ${L.cdd} pinned`;
  const cddOk = Boolean(activeCdd);

  const moduleStatusBanner = () => {
    if (!selectedModuleOpt) return null;
    if (selectedModuleOpt.isCourseEnd) {
      return (
        <div className={styles.statusInfo}>
          📋 Title-end item selected: <strong>{selectedModuleOpt.courseEndLabel}</strong>
        </div>
      );
    }
    const num = selectedModuleOpt.key;
    const unit = selectedModuleOpt.isDay ? 'Day' : 'Module';
    if (existingNums.has(num)) {
      return (
        <div className={styles.statusWarn}>
          ⚠️ {L.blueprint} already exists for {unit} {num}. Generating again will create a new version.
        </div>
      );
    }
    return (
      <div className={styles.statusOk}>
        ✅ Ready to generate {unit} {num} {L.blueprint}.
      </div>
    );
  };

  const previewFullContent = versionDetail?.full_content
    || displayBp?.active_content?.full_content
    || '';
  const previewSections = versionDetail?.sections
    || displayBp?.active_content?.sections;

  function parseGenParams(raw) {
    if (!raw) return {};
    if (typeof raw === 'object') return raw;
    try { return JSON.parse(raw); } catch { return {}; }
  }

  const viewMode = parseGenParams(versionDetail?.generation_params).mode
    || parseGenParams(displayBp?.active_content?.generation_params).mode
    || genMode;

  return (
    <PageContainer
      title=""
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: L.blueprint }]}
      noPadding
    >
      <div className={styles.page}>
        <SectionBadge
          icon="🧩"
          title={`Module ${L.blueprint}`}
          subtitle={`Generates directly from the linked ${L.cdd} — module structure, lessons, and all components are derived automatically. No need to re-enter ${L.titleLower} metadata.`}
        />

        <div className={styles.configPanel}>
          <div className={styles.configPanel__heading}>Approved Configuration (Read-Only)</div>
          <div className={styles.configPanel__row}>
            <div>
              <div className={styles.configPanel__item}>🎨 {L.style}</div>
              <div className={styles.configPanel__value} style={{ color: styleOk ? '#10b981' : '#f59e0b' }}>
                {styleLabel}
                <span className={styles.configPanel__status} style={{ color: styleOk ? '#10b981' : '#f59e0b' }}>
                  {styleOk ? '● Active' : '○ Not set'}
                </span>
              </div>
            </div>
            <div>
              <div className={styles.configPanel__item}>📘 {L.cdd}</div>
              <div className={styles.configPanel__value} style={{ color: cddOk ? '#6366f1' : '#f59e0b' }}>
                {cddLabel}
                <span className={styles.configPanel__status} style={{ color: cddOk ? '#6366f1' : '#f59e0b' }}>
                  {cddOk ? `📌 Pinned (${activeCdd.active_version || 'v1'})` : '○ Not pinned'}
                </span>
              </div>
            </div>
          </div>
        </div>

        {!isAdmin && (
          activeCdd ? (
            <div className={styles.ctaBanner}>
              👇 <strong>Generate {L.blueprint} from Approved {L.cdd}:</strong>
              {' '}Use the form below to create a new module {L.blueprintLower} based on the pinned {L.cdd} above.
            </div>
          ) : (
            <div className={styles.warnBanner}>
              No {L.cdd} is pinned yet. Create and pin a {L.cdd} in the {L.cdd} tab first, then return here to generate {L.blueprintsLower}.
            </div>
          )
        )}

        <div className={styles.modeRadio} role="radiogroup" aria-label="Generation mode">
          <label>
            <input
              type="radio"
              name="bp_mode"
              checked={genMode === GENERATION_MODES.STUDENT}
              onChange={() => dispatch(setGenerationMode(GENERATION_MODES.STUDENT))}
            />
            Student
          </label>
          <label>
            <input
              type="radio"
              name="bp_mode"
              checked={genMode === GENERATION_MODES.TEACHER}
              onChange={() => dispatch(setGenerationMode(GENERATION_MODES.TEACHER))}
            />
            Teacher
          </label>
        </div>

        <div className={styles.layout}>
          <details className={styles.accordion} open>
            <summary className={styles.accordion__summary}>➕ Create New {L.blueprint}</summary>
            <div className={styles.accordion__body}>
              <Select
                label={`📘 Source ${L.cdd}`}
                options={cddOptions}
                value={linkedCddId != null ? String(linkedCddId) : NONE_CDD}
                onChange={(e) => {
                  const v = e.target.value;
                  setLinkedCddId(v ? Number(v) : null);
                  setModuleSelKey('');
                  setModuleConfirmed(false);
                }}
              />

              {moduleOptions.length > 0 ? (
                <>
                  <div className={styles.moduleLabel}>{isDluCdd ? 'Select Day' : 'Select Module / Title-End Item'}</div>
                  <Select
                    label=""
                    options={moduleOptions.map((o) => ({
                      value: String(o.key),
                      label: o.label,
                    }))}
                    value={moduleSelKey || String(moduleOptions[0].key)}
                    onChange={(e) => {
                      setModuleSelKey(e.target.value);
                      setModuleConfirmed(false);
                    }}
                  />
                  {moduleConfirmed ? (
                    <div className={styles.confirmOk}>✅ {isDluCdd ? 'Day' : 'Module'} selection confirmed.</div>
                  ) : (
                    moduleStatusBanner()
                  )}
                </>
              ) : (
                <p className={styles.configPanel__item}>
                  ℹ️ Module number will be 1 (no module structure detected in {L.cdd}).
                </p>
              )}

              <Input
                label={`${L.blueprint} Title (optional)`}
                placeholder={
                  selectedModuleOpt
                    ? `e.g. ${selectedModuleOpt.isDay ? 'Day' : 'Module'} ${selectedModuleOpt.key} — ${linkedCdd?.course_title || linkedCdd?.title || L.blueprint}`
                    : `e.g. Module 1 — Patient Assessment ${L.blueprint}`
                }
                value={documentTitle}
                onChange={(e) => setDocumentTitle(e.target.value)}
              />

              <Button variant="secondary" className={styles.requestChangeBtn} type="button" onClick={onConfirmModule}>
                {isDluCdd ? 'Confirm Day Selection' : 'Confirm Module Selection'}
              </Button>

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
                  placeholder="e.g. Focus on simulation-based lessons. Add a career spotlight per lesson."
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
                      placeholder="e.g. Simulation-based lessons"
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

              {error && (
                <ErrorState message={error} onRetry={() => dispatch(fetchBlueprintsThunk(courseId))} />
              )}
            </div>
          </details>

          <details className={styles.accordion}>
            <summary className={styles.accordion__summary}>📂 Your Module {L.blueprints}</summary>
            <div className={styles.accordion__body}>
              {isLoading ? (
                <div className={styles.center}><Loader size="lg" /></div>
              ) : (blueprints.length === 0 && archivedBlueprints.length === 0) ? (
                <EmptyState
                  title={`No ${L.blueprints} yet`}
                  message={`Create your first ${L.blueprint} using the form above.`}
                />
              ) : (
                <>
                  {blueprints.length > 0 && (
                  <Select
                    label={`Select ${L.blueprint} to View/Edit`}
                    // Title and module alone do not identify a row — a course
                    // can hold twenty blueprints for the same module. The date
                    // and author are what make the option pickable.
                    options={blueprints.map((bp) => ({
                      value: String(bp.id),
                      label: [
                        `M${bp.module_number || '?'}: ${bp.title} (ID: ${bp.id})`,
                        formatDate(bp.created_at),
                        bp.created_by,
                      ].filter(Boolean).join(' — '),
                    }))}
                    value={viewBpId != null ? String(viewBpId) : ''}
                    onChange={(e) => {
                      const id = Number(e.target.value);
                      setViewBpId(id);
                      setSelectedBpId(id);
                      if (id) dispatch(fetchBlueprintVersionsThunk(id));
                    }}
                  />
                  )}

                  {/* Outside the `displayBp` guard below and not conditioned on
                      the live list: archiving the last live blueprint would
                      otherwise take the archived list off screen with it,
                      leaving no way to restore what was just archived. */}
                  <DocumentArchivePanel
                    label={L.blueprint}
                    docs={blueprints}
                    archivedDocs={archivedBlueprints}
                    activeId={activeBlueprint?.id ?? null}
                    selectedId={viewBpId}
                    busy={isArchiving}
                    canPurge={isAdmin}
                    onSelect={(id) => {
                      setViewBpId(id);
                      setSelectedBpId(id);
                      dispatch(fetchBlueprintVersionsThunk(id));
                    }}
                    onSetActive={onPin}
                    onArchive={onArchiveBlueprint}
                    onRestore={onRestoreBlueprint}
                    onPurge={onPurgeBlueprint}
                    onBulkArchive={onBulkArchiveBlueprints}
                    refusal={archiveRefusal}
                    onDismissRefusal={() => dispatch(clearArchiveRefusal())}
                  />

                  {displayBp && (
                    <>
                      <div className={styles.metrics}>
                        <div className={styles.metric}>
                          <span className={styles.metric__label}>Active Version</span>
                          <span className={styles.metric__value}>{displayBp.active_version || '—'}</span>
                        </div>
                        <div className={styles.metric}>
                          <span className={styles.metric__label}>State</span>
                          <span className={styles.metric__value}>
                            {(displayBp.workflow_state || 'draft').replace(/^\w/, (c) => c.toUpperCase())}
                          </span>
                        </div>
                        <div className={styles.metric}>
                          <span className={styles.metric__label}>Total Versions</span>
                          <span className={styles.metric__value}>{versions.length}</span>
                        </div>
                      </div>

                      {displayBp.cdd_id && (() => {
                        const bpCdd = cdds.find((c) => c.id === displayBp.cdd_id);
                        if (!bpCdd) return null;
                        return (
                          <div className={styles.cddLink}>
                            🔗 Linked to {L.cdd}: <strong>{bpCdd.title || bpCdd.course_title}</strong>
                            {' '}({bpCdd.active_version || 'v1'})
                          </div>
                        );
                      })()}

                      {versions.length > 0 && (
                        <div className={styles.versionRow}>
                          <Select
                            label="View Version"
                            options={versions.map((v) => ({
                              value: v.version,
                              label: `${v.version}${v.is_active ? ' (active)' : ''}`,
                            }))}
                            value={viewVersion || displayBp.active_version || ''}
                            onChange={(e) => setViewVersion(e.target.value)}
                          />
                          {viewVersion && viewVersion !== displayBp.active_version && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => dispatch(activateBlueprintVersionThunk({
                                blueprintId: displayBp.id,
                                version: viewVersion,
                              }))}
                            >
                              Set as Active Version
                            </Button>
                          )}
                        </div>
                      )}

                      <div className={styles.activeContent}>
                        <h3 className={styles.activeContent__title}>{displayBp.title}</h3>
                        {viewMode && (
                          <p className={styles.configPanel__item}>
                            Mode: <strong>{viewMode === 'teacher' ? 'Teacher' : 'Student'}</strong>
                          </p>
                        )}

                        <PromoteOverrideButton
                          sourceType="blueprint"
                          artifactId={displayBp?.id}
                          version={versionDetail?.version || displayBp?.active_content?.version}
                          generationParams={versionDetail?.generation_params
                            || displayBp?.active_content?.generation_params}
                        />
                        <BlueprintContentView
                          fullContent={previewFullContent}
                          sections={previewSections}
                          editable
                          saving={savingSection}
                          onSaveSection={onSaveBlueprintSection}
                          onRegenerateSection={onRegenerateBlueprintSection}
                          onRegenerateItem={onRegenerateBlueprintItem}
                        />

                        <button
                          type="button"
                          className={styles.saveVersionToggle}
                          onClick={() => setShowSaveVersion((v) => !v)}
                        >
                          🚀 Save as New Version {showSaveVersion ? '▾' : '▸'}
                        </button>
                        {showSaveVersion && (
                          <div className={styles.saveVersion}>
                            <form className={styles.form} onSubmit={versionForm.handleSubmit(onCommitVersion)}>
                              <Input
                                label="New Version Tag"
                                placeholder={`v${versions.length + 1}`}
                                {...versionForm.register('tag')}
                              />
                              <Input
                                label="Change Reason"
                                required
                                placeholder="What changed?"
                                error={versionForm.formState.errors.reason?.message}
                                {...versionForm.register('reason')}
                              />
                              <Button type="submit" variant="primary" size="sm">
                                💾 Commit New {L.blueprint} Version
                              </Button>
                            </form>
                          </div>
                        )}
                      </div>

                      <Button
                        variant="primary"
                        className={styles.activeBlueprintBtn}
                        onClick={() => onPin(displayBp.id)}
                        disabled={activeBlueprint?.id === displayBp.id}
                      >
                        📌 Set as Active {L.blueprint} for Generation
                      </Button>

                      <div className={styles.downloadBlock}>
                        <p className={styles.downloadBlock__title}>📥 Download {L.blueprint}</p>
                        <div className={styles.downloadBlock__row}>
                          <Button variant="secondary" fullWidth onClick={() => onExport('docx')}>
                            ⬇️ Word (.docx)
                          </Button>
                          <Button variant="secondary" fullWidth onClick={() => onExport('xlsx')}>
                            ⬇️ Excel (.xlsx)
                          </Button>
                          <Button variant="secondary" fullWidth onClick={handleDownloadPrompt}>
                            ⬇️ Download Prompt Used (.doc)
                          </Button>
                        </div>
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
          </details>

          <details className={styles.accordion}>
            <summary className={styles.accordion__summary}>🎯 {L.blueprint} Prompts</summary>
            <div className={styles.accordion__body}>
              <InlinePromptControls
                component="blueprint"
                embedded
                extraInstructions={extraInstructions}
                showExtraInstructions={false}
                onPromptsChange={setPromptConfig}
                headerHint={`📝 Select the source ${L.cdd} and module above, then configure the prompt and generate your ${L.blueprint} below.`}
              />

              {activeStyle && (
                <div className={styles.styleBanner}>
                  🎨 <strong>{L.style} &quot;{activeStyle.name}&quot;</strong> will be applied to this {L.blueprint}.
                </div>
              )}

              <div className={styles.generateRow}>
                <Button
                  variant="primary"
                  size="md"
                  className={styles.generateRow__main}
                  loading={isGenerating}
                  onClick={onGenerate}
                >
                  {isGenerating ? `Generating ${L.blueprint}…` : `🤖 Generate ${L.blueprint} with AI`}
                </Button>
                <Button variant="secondary" size="md" onClick={handleDownloadPrompt}>
                  ⬇️ Download Prompt
                </Button>
              </div>

              {digestPipelineEnabled && (
                <BlockWidePanel
                  label={L.blueprint}
                  hint={`Generate a whole-block ${L.blueprint} — a day-by-day plan built from every source in the block via enumerate → digest → reduce, with coverage checks. Applies the active ${L.styleLower} and the additional instructions set above. Runs in the background and is pinned as active when it finishes.`}
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
    </PageContainer>
  );
}
