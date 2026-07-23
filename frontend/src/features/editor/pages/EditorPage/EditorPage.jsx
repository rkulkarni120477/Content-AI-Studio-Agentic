import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchGenerationsThunk,
  fetchGenerationBlocksThunk,
  fetchCourseBlocksThunk,
  validateCourseThunk,
  validateGenerationThunk,
  exportCourseThunk,
  exportGenerationThunk,
} from '@features/editor/editorThunks';
import {
  selectGenerations,
  selectSelectedGenerationId,
  selectBlocks,
  selectCourseBlocks,
  selectValidation,
  selectGenValidation,
  selectEditorLoading,
  selectEditorLoadingBlocks,
  selectEditorExporting,
  selectEditorValidating,
  selectEditorError,
  setSelectedGeneration as setSelectedGenerationAction,
  clearError as clearEditorError,
} from '@features/editor/editorSlice';
import { selectActiveCdd } from '@features/cdd/cddSlice';
import { selectActiveBlueprint, selectBlueprints } from '@features/blueprint/blueprintSlice';
import { selectLatestGenerationId } from '@features/generate/generateSlice';
import { fetchBlueprintsThunk } from '@features/blueprint/blueprintThunks';
import { fetchCddsThunk } from '@features/cdd/cddThunks';
import {
  selectSelectedProject,
  selectSelectedCourse,
  selectSelectedCluster,
} from '@features/dashboard/dashboardSlice';
import { editorService } from '@features/editor/services/editorService';
import { blueprintService } from '@features/blueprint/services/blueprintService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { downloadBlob } from '@utils/helpers';
import { EXPORT_TEMPLATES, WORKFLOW_EXPORTABLE } from '@utils/constants';
import { formatDateTime, extractErrorMessage } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import ValidationPanel from '@features/editor/components/ValidationPanel/ValidationPanel';
import GenerationTraceBar from '@features/editor/components/GenerationTraceBar/GenerationTraceBar';
import ExportTileButton from '@features/editor/components/ExportTileButton/ExportTileButton';
import PromptDownloadButton from '@features/editor/components/PromptDownloadButton/PromptDownloadButton';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import Loader from '@components/common/Loader/Loader';
import EditorBlockCard from '@features/editor/components/EditorBlockCard/EditorBlockCard';
import styles from './EditorPage.module.scss';

const LAST_GEN_KEY = (courseId) => `content_ai_last_gen_${courseId}`;

function validationSummary(vr) {
  if (!vr?.summary) return { errors: 0, warnings: 0, passed: vr?.passed ?? true };
  return {
    errors: vr.summary.errors ?? 0,
    warnings: vr.summary.warnings ?? 0,
    passed: vr.passed ?? (vr.summary.errors === 0),
  };
}

function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

export default function EditorPage() {
  const { courseId } = useParams();
  const dispatch = useAppDispatch();
  const { isAdmin } = useAuth();

  const generations = useAppSelector(selectGenerations);
  const selectedGenId = useAppSelector(selectSelectedGenerationId);
  const blocks = useAppSelector(selectBlocks);
  const courseBlocks = useAppSelector(selectCourseBlocks);
  const validation = useAppSelector(selectValidation);
  const genValidation = useAppSelector(selectGenValidation);
  const isLoading = useAppSelector(selectEditorLoading);
  const isLoadingBlocks = useAppSelector(selectEditorLoadingBlocks);
  const isExporting = useAppSelector(selectEditorExporting);
  const isValidating = useAppSelector(selectEditorValidating);
  const error = useAppSelector(selectEditorError);

  const activeCdd = useAppSelector(selectActiveCdd);
  const activeBlueprint = useAppSelector(selectActiveBlueprint);
  const allBlueprints = useAppSelector(selectBlueprints);
  const selProject = useAppSelector(selectSelectedProject);
  const selCourse = useAppSelector(selectSelectedCourse);
  const selCluster = useAppSelector(selectSelectedCluster);
  const latestGenerationId = useAppSelector(selectLatestGenerationId);

  const [genDetail, setGenDetail] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [courseTemplate, setCourseTemplate] = useState('default');
  const [genTemplate, setGenTemplate] = useState('default');
  const [courseExportOk, setCourseExportOk] = useState(true);
  const [pendingDownload, setPendingDownload] = useState(null);
  const [selectedModuleId, setSelectedModuleId] = useState('');
  const [moduleExporting, setModuleExporting] = useState(false);
  const [moduleExportError, setModuleExportError] = useState(null);

  const numericCourseId = Number(courseId);

  const loadGenerations = useCallback(async () => {
    if (!numericCourseId || Number.isNaN(numericCourseId)) return [];
    dispatch(clearEditorError());

    let projectId = selProject?.id ?? selCourse?.project_id;
    if (!projectId) {
      try {
        const course = await dashboardService.getCourse(numericCourseId);
        projectId = course?.project_id;
      } catch {
        /* course lookup optional */
      }
    }

    // Fetch across the whole course (not scoped to the currently pinned Blueprint/CDD)
    // so lessons generated from any module show up here.
    const list = await dispatch(fetchGenerationsThunk({
      courseId: numericCourseId,
      projectId: projectId ?? undefined,
      pageSize: 500,
    })).unwrap();

    const filtered = normalizeList(list);
    const lastId = sessionStorage.getItem(LAST_GEN_KEY(courseId));
    // Prefer the freshly generated one, then sessionStorage, then first in list
    const pickId = (latestGenerationId && filtered.some((g) => g.id === latestGenerationId))
      ? latestGenerationId
      : (lastId && filtered.some((g) => String(g.id) === lastId)
        ? Number(lastId)
        : filtered[0]?.id);
    if (pickId) dispatch(setSelectedGenerationAction(pickId));
    return filtered;
  }, [
    dispatch,
    numericCourseId,
    courseId,
    selProject?.id,
    selCourse?.project_id,
    latestGenerationId,
  ]);

  useEffect(() => {
    if (!numericCourseId || Number.isNaN(numericCourseId)) return;
    dispatch(fetchBlueprintsThunk(numericCourseId));
    dispatch(fetchCddsThunk(numericCourseId));
    loadGenerations().catch(() => {});
    dispatch(fetchCourseBlocksThunk(numericCourseId)).unwrap().catch(() => {});
  }, [dispatch, numericCourseId, loadGenerations]);

  useEffect(() => {
    if (!selectedGenId) {
      setGenDetail(null);
      return;
    }
    sessionStorage.setItem(LAST_GEN_KEY(courseId), String(selectedGenId));
    dispatch(fetchGenerationBlocksThunk(selectedGenId));
    editorService.getGeneration(selectedGenId).then(setGenDetail).catch(() => setGenDetail(null));
  }, [selectedGenId, courseId, dispatch]);

  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return undefined;
    }
    const t = setTimeout(() => {
      setSearchLoading(true);
      editorService.searchBlocks(searchQuery.trim())
        .then((res) => setSearchResults(normalizeList(res)))
        .catch(() => setSearchResults([]))
        .finally(() => setSearchLoading(false));
    }, 300);
    return () => clearTimeout(t);
  }, [searchQuery]);

  const displayGens = generations;

  const selectedGen = useMemo(
    () => displayGens.find((g) => g.id === selectedGenId) || genDetail,
    [displayGens, selectedGenId, genDetail],
  );

  const allCourseApproved = useMemo(() => {
    const all = courseBlocks.length > 0 ? courseBlocks : blocks;
    return all.length > 0 && all.every((b) => WORKFLOW_EXPORTABLE.includes(b.workflow_state));
  }, [courseBlocks, blocks]);

  const courseComplete = displayGens.length > 0 && allCourseApproved;

  const allGenExportable = blocks.length > 0
    && blocks.every((b) => WORKFLOW_EXPORTABLE.includes(b.workflow_state));

  const canExportGen = isAdmin || allGenExportable;
  const genValSum = validationSummary(genValidation);
  const exportBlockedByValidation = genValSum.errors > 0;
  const canExportGenFinal = canExportGen && !exportBlockedByValidation;

  const courseValSum = validationSummary(validation);

  useEffect(() => {
    setCourseExportOk(!validation || courseValSum.errors === 0);
  }, [validation, courseValSum.errors]);

  const traceCdd = useMemo(() => {
    if (!genDetail?.cdd_id) return 'None';
    if (activeCdd?.id === genDetail.cdd_id) {
      const t = activeCdd.title || activeCdd.course_title || 'CDD';
      return `${t} (${genDetail.cdd_version || activeCdd.active_version || 'v1'})`;
    }
    return `CDD #${genDetail.cdd_id}${genDetail.cdd_version ? ` (${genDetail.cdd_version})` : ''}`;
  }, [genDetail, activeCdd]);

  const traceBp = useMemo(() => {
    if (!genDetail?.blueprint_id) return 'None';
    if (activeBlueprint?.id === genDetail.blueprint_id) {
      return `${activeBlueprint.title} (${genDetail.blueprint_version || activeBlueprint.active_version || 'v1'})`;
    }
    return `Blueprint #${genDetail.blueprint_id}${genDetail.blueprint_version ? ` (${genDetail.blueprint_version})` : ''}`;
  }, [genDetail, activeBlueprint]);

  function onGenChange(e) {
    const id = Number(e.target.value);
    if (id) dispatch(setSelectedGenerationAction(id));
  }

  async function onExportCourse(fmt) {
    const name = (selCourse?.title || selCourse?.name || 'course').replace(/\s+/g, '_');
    await dispatch(exportCourseThunk({
      courseId: numericCourseId,
      format: fmt,
      template: courseTemplate,
      filename: `${name}_full_course.${fmt === 'zip' ? 'zip' : fmt}`,
    }));
  }

  async function onExportGen(fmt) {
    if (!selectedGenId || !canExportGenFinal) return;
    const topic = (selectedGen?.topic || 'generation').replace(/\s+/g, '_');
    const filename = `${topic}.${fmt}`;
    try {
      const response = await editorService.exportGeneration(selectedGenId, fmt, genTemplate);
      setPendingDownload({ blob: response.data, filename, format: fmt });
    } catch {
      await dispatch(exportGenerationThunk({
        generationId: selectedGenId,
        format: fmt,
        template: genTemplate,
        filename,
      }));
    }
  }

  const scopeLabel = activeBlueprint
    ? `🧩 Generate tab is pinned to: ${activeBlueprint.title || 'Active Blueprint'}`
    : activeCdd
      ? `📘 Generate tab is pinned to: ${activeCdd.title || activeCdd.course_title || 'Active CDD'}`
      : null;

  // Course-scoped blueprints, keyed by id. A generation's blueprint_id can also
  // point at a blueprint outside this course (cross-course override at generation
  // time) — those get fetched individually into extraBlueprintMeta below so the
  // dropdown can still show their real module name instead of "Other / Unlinked".
  const blueprintMeta = useMemo(() => {
    const map = new Map();
    allBlueprints.forEach((bp) => map.set(bp.id, bp));
    return map;
  }, [allBlueprints]);

  const [extraBlueprintMeta, setExtraBlueprintMeta] = useState({});
  const fetchedBlueprintIdsRef = useRef(new Set());

  useEffect(() => {
    const missing = [];
    displayGens.forEach((g) => {
      if (
        g.blueprint_id
        && !blueprintMeta.has(g.blueprint_id)
        && !fetchedBlueprintIdsRef.current.has(g.blueprint_id)
      ) {
        missing.push(g.blueprint_id);
      }
    });
    if (missing.length === 0) return;
    missing.forEach((id) => fetchedBlueprintIdsRef.current.add(id));
    Promise.all(missing.map((id) => blueprintService.getBlueprint(id)
      .then((bp) => [id, bp])
      .catch(() => [id, null])))
      .then((pairs) => {
        setExtraBlueprintMeta((prev) => {
          const next = { ...prev };
          pairs.forEach(([id, bp]) => { if (bp) next[id] = bp; });
          return next;
        });
      });
  }, [displayGens, blueprintMeta]);

  const genGroups = useMemo(() => {
    if (!displayGens.length) return [];
    const groups = new Map();
    displayGens.forEach((g) => {
      const bp = g.blueprint_id
        ? (blueprintMeta.get(g.blueprint_id) || extraBlueprintMeta[g.blueprint_id] || null)
        : null;
      const key = bp ? `bp-${bp.id}` : 'other';
      if (!groups.has(key)) {
        groups.set(key, {
          bpId: bp?.id ?? null,
          order: bp ? (bp.module_number ?? 999) : 1000,
          label: bp ? (bp.title || `Module ${bp.module_number ?? '?'}`) : 'Other / Unlinked',
          options: [],
        });
      }
      groups.get(key).options.push({
        value: String(g.id),
        label: `${g.topic} (ID: #${g.id}) — ${g.created_at ? formatDateTime(g.created_at) : ''}`,
      });
    });
    return Array.from(groups.values())
      .sort((a, b) => a.order - b.order)
      .map((grp) => ({
        ...grp,
        // Ascending ID so chapters follow blueprint order (#174, #175, …), not newest-first.
        options: [...grp.options].sort((a, b) => Number(a.value) - Number(b.value)),
      }));
  }, [displayGens, blueprintMeta, extraBlueprintMeta]);

  // Modules with at least one generated lesson — feeds the "Download Module Lessons" dropdown.
  const moduleOptions = useMemo(
    () => genGroups
      .filter((grp) => grp.bpId != null)
      .map((grp) => ({ value: String(grp.bpId), label: grp.label })),
    [genGroups],
  );

  useEffect(() => {
    if (moduleOptions.length === 0) {
      setSelectedModuleId('');
      return;
    }
    if (!moduleOptions.some((m) => m.value === selectedModuleId)) {
      setSelectedModuleId(moduleOptions[0].value);
    }
  }, [moduleOptions, selectedModuleId]);

  async function onExportModuleLessons(fmt) {
    if (!selectedModuleId) return;
    setModuleExporting(true);
    setModuleExportError(null);
    try {
      const bpLabel = moduleOptions.find((m) => m.value === selectedModuleId)?.label || 'module';
      const response = await blueprintService.exportModuleLessons(Number(selectedModuleId), fmt);
      const filename = `${bpLabel.replace(/\s+/g, '_')}_lessons.${fmt}`;
      downloadBlob(response.data, filename);
    } catch (e) {
      setModuleExportError(extractErrorMessage(e));
    } finally {
      setModuleExporting(false);
    }
  }

  const errorMessage = error
    ? (typeof error === 'string' ? error : extractErrorMessage(error))
    : null;

  const exportAccordion = (moduleOptions.length > 0 || displayGens.length > 0) ? (
    <details className={styles.streamlitExpander}>
      <summary className={styles.streamlitExpander__summary}>📦 Export</summary>
      <div className={styles.streamlitExpander__body}>
        {!canExportGen && selectedGenId && !isAdmin && (
          <p className={styles.exportLock}>
            🔒 Export locked — all blocks must be <strong>Approved</strong> or <strong>Published</strong> before exporting. Submit content for review and get it approved first.
          </p>
        )}
        <div className={styles.topExportRow}>
          {moduleOptions.length > 0 && (
            <div className={styles.moduleExportPanel}>
              <p className={styles.moduleExportPanel__title}>
                📦 Download all lessons from a module in one file
              </p>
              <Select
                wrapperClassName={styles.moduleExportPanel__moduleSelect}
                options={moduleOptions}
                value={selectedModuleId}
                onChange={(e) => setSelectedModuleId(e.target.value)}
              />
              {moduleExportError && (
                <p className={styles.exportBlockErr}>⚠️ {moduleExportError}</p>
              )}
              <div className={styles.exportGrid2}>
                <ExportTileButton
                  format="md"
                  label="Markdown"
                  loading={moduleExporting}
                  disabled={!selectedModuleId}
                  onClick={() => onExportModuleLessons('md')}
                />
                <ExportTileButton
                  format="docx"
                  label="DOCX"
                  loading={moduleExporting}
                  disabled={!selectedModuleId}
                  onClick={() => onExportModuleLessons('docx')}
                />
              </div>
            </div>
          )}

          {displayGens.length > 0 && (
            <div className={styles.genExportPanel}>
              {exportBlockedByValidation && canExportGen && (
                <p className={styles.exportBlockErr}>
                  ❌ Export blocked — {genValSum.errors} error(s) found. Run Validate in the Plagiarism &amp; Citation Dashboard.
                </p>
              )}
              <Select
                label="Export template"
                options={Object.entries(EXPORT_TEMPLATES).map(([value, label]) => ({ value, label }))}
                value={genTemplate}
                onChange={(e) => setGenTemplate(e.target.value)}
                disabled={!canExportGenFinal}
              />
              <div className={styles.exportGrid5}>
                {[
                  { fmt: 'md', label: 'MD' },
                  { fmt: 'json', label: 'JSON' },
                  { fmt: 'html', label: 'HTML' },
                  { fmt: 'docx', label: 'DOCX' },
                  { fmt: 'xlsx', label: 'XLSX' },
                  { fmt: 'pdf', label: 'PDF' },
                ].map(({ fmt, label }) => (
                  <ExportTileButton
                    key={fmt}
                    format={fmt}
                    label={label}
                    disabled={!canExportGenFinal}
                    loading={isExporting}
                    onClick={() => onExportGen(fmt)}
                  />
                ))}
              </div>
              {pendingDownload && (
                <div className={styles.downloadRow}>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => {
                      downloadBlob(pendingDownload.blob, pendingDownload.filename);
                      setPendingDownload(null);
                    }}
                  >
                    ⬇️ Download {pendingDownload.format.toUpperCase()}
                  </Button>
                  <Button variant="ghost" size="xs" onClick={() => setPendingDownload(null)}>
                    Dismiss
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </details>
  ) : null;

  return (
    <PageContainer
      title=""
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Editor' }]}
      noPadding
    >
      <div className={styles.page}>
        {courseComplete && (
          <div className={styles.completionBanner}>
            <div className={styles.completionBanner__icon}>🎉</div>
            <div className={styles.completionBanner__body}>
              <strong>Title Generation Complete!</strong>
              <p>All blocks are approved. Validate content below before downloading the full title package.</p>
              <div className={styles.completionBanner__valRow}>
                <Button
                  variant="primary"
                  size="sm"
                  loading={isValidating}
                  onClick={() => dispatch(validateCourseThunk(numericCourseId))}
                >
                  🔍 Validate Content
                </Button>
                {validation && (
                  <span className={`${styles.valBadge} ${courseValSum.errors ? styles.valBadge__err : courseValSum.warnings ? styles.valBadge__warn : styles.valBadge__ok}`}>
                    {courseValSum.errors
                      ? `❌ ${courseValSum.errors} error(s) — fix before exporting`
                      : courseValSum.warnings
                        ? `⚠️ ${courseValSum.warnings} warning(s) — confirm to export`
                        : '✅ Validation passed — ready to export'}
                  </span>
                )}
                {!validation && (
                  <span className={styles.valBadgeCaption}>
                    Click <strong>Validate Content</strong> to check for issues before exporting.
                  </span>
                )}
              </div>
              {validation && <ValidationPanel result={validation} />}
              {!validation && (
                <div className={styles.valInfo}>
                  💡 Run <strong>Validate Content</strong> first for a quality check. You can still export, but unvalidated content may contain issues.
                </div>
              )}
              {courseExportOk && (
                <>
                  <Select
                    label="Export template"
                    options={Object.entries(EXPORT_TEMPLATES).map(([value, label]) => ({ value, label }))}
                    value={courseTemplate}
                    onChange={(e) => setCourseTemplate(e.target.value)}
                    wrapperClassName={styles.templateSelect}
                  />
                  <div className={styles.exportGrid4}>
                    {['md', 'html', 'docx', 'xlsx', 'zip'].map((fmt) => (
                      <ExportTileButton key={fmt} format={fmt} label={fmt.toUpperCase()} loading={isExporting} onClick={() => onExportCourse(fmt)} />
                    ))}
                  </div>
                </>
              )}
              <div className={styles.promptDlRow}>
                <PromptDownloadButton
                  promptName={genDetail?.prompt_name || selectedGen?.prompt_name}
                  promptVersion={genDetail?.prompt_version || selectedGen?.prompt_version}
                  projectName={selProject?.name}
                  clusterName={selCluster?.name}
                  courseName={selCourse?.title || selCourse?.name}
                />
              </div>
            </div>
          </div>
        )}

        <SectionBadge
          icon="✏️"
          title="Content Editor & Export"
          subtitle="Review, edit, and refine generated blocks. Submit quality reviews, run AI evaluations, and export to Markdown, JSON, HTML, or DOCX."
        />

        <div className={styles.filterRow}>
          <div className={styles.filterRow__col}>
            <label className={styles.searchRow__label} htmlFor="editor-search">
              Search blocks by content or label
            </label>
            <input
              id="editor-search"
              className={styles.searchInput}
              type="search"
              placeholder="Search blocks by content or label"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <div className={styles.filterRow__col}>
            {isLoading ? (
              <Loader size="sm" />
            ) : displayGens.length === 0 ? (
              <p className={styles.noGens}>No generations found yet.</p>
            ) : (
              <Select
                label="Select File (Topic)"
                groups={genGroups}
                value={selectedGenId ? String(selectedGenId) : ''}
                onChange={onGenChange}
              />
            )}
          </div>
        </div>

        {searchQuery && (
          <div className={styles.searchResults}>
            {searchLoading ? (
              <p className={styles.searchEmpty}>Searching…</p>
            ) : searchResults?.length > 0 ? (
              <>
                <p className={styles.searchFound}>Found {searchResults.length} matching block(s):</p>
                <table className={styles.searchTable}>
                  <thead>
                    <tr><th>ID</th><th>Label</th><th>State</th><th>Gen#</th></tr>
                  </thead>
                  <tbody>
                    {searchResults.map((b) => (
                      <tr key={b.id}>
                        <td>{b.id}</td>
                        <td>{b.block_label}</td>
                        <td>{b.workflow_state}</td>
                        <td>{b.generation_id ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : (
              <p className={styles.searchEmpty}>No blocks match your search.</p>
            )}
          </div>
        )}

        {scopeLabel && (
          <div className={styles.scopeBanner}>
            <strong>{scopeLabel}</strong>
            <span> — the file list below includes lessons from every module in this title.</span>
          </div>
        )}

        {selectedGenId && (
          <GenerationTraceBar
            promptName={genDetail?.prompt_name || selectedGen?.prompt_name}
            promptVersion={genDetail?.prompt_version || selectedGen?.prompt_version}
            cddLabel={traceCdd}
            blueprintLabel={traceBp}
          />
        )}

        {errorMessage && (
          <div className={styles.inlineError}>
            <span>⚠️ {errorMessage}</span>
            <Button variant="ghost" size="xs" onClick={() => loadGenerations().catch(() => {})}>
              Try Again
            </Button>
          </div>
        )}

        {isLoadingBlocks && (
          <div className={styles.loadingRow}><Loader size="sm" /> Loading blocks…</div>
        )}

        {!isLoadingBlocks && selectedGenId && blocks.length === 0 && !errorMessage && (
          <div className={styles.tip}>
            💡 <strong>Tip:</strong> No blocks here yet. Go to the <strong>Generate Title</strong> tab, create content, and it will appear here for editing.
          </div>
        )}

        {selectedGenId && blocks.length > 0 && (
          <p className={styles.blockCaption}>
            📝 Showing {blocks.length} block(s) for Generation #{selectedGenId}
          </p>
        )}

        {blocks.map((block, idx) => (
          <EditorBlockCard
            key={block.id}
            block={block}
            generationId={selectedGenId}
            genCreatedBy={genDetail?.created_by}
            onBlockUpdated={() => dispatch(fetchGenerationBlocksThunk(selectedGenId))}
            isValidating={isValidating}
            genValidation={genValidation}
            onValidate={() => dispatch(validateGenerationThunk(selectedGenId))}
            exportSlot={idx === blocks.length - 1 ? exportAccordion : null}
          />
        ))}

        {blocks.length === 0 && exportAccordion}
      </div>
    </PageContainer>
  );
}
