import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { selectSelectedCluster } from '@features/dashboard/dashboardSlice';
import { isClusterPromptApiAvailable } from '@features/clusterPrompt/clusterPromptApiDeps';
import { clusterPromptService } from '@features/clusterPrompt/clusterPromptService';
import {
  fetchStylesThunk, activateStyleThunk,
  deactivateStyleThunk, fetchDocumentsThunk, uploadDocumentsThunk,
  regenerateStyleThunk,
  refineStyleThunk,
  deleteStyleThunk,
  uploadStyleDocsThunk,
} from '@features/style/styleThunks';
import { deleteDocumentThunk } from '@features/style/styleThunks';
import { styleService } from '@features/style/services/styleService';
import {
  selectStyles, selectDocuments, selectDocumentStats, selectActiveStyle,
  selectStyleLoading, selectGeneratingStyleId, selectStyleError,
} from '@features/style/styleSlice';
import { selectIsReviewer, selectIsAdmin } from '@features/auth/authSlice';
import { formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import MultiSelect from '@components/common/MultiSelect/MultiSelect';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ErrorState from '@components/common/ErrorState/ErrorState';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import SearchBar from '@components/common/SearchBar/SearchBar';
import PromptLibraryPanel from '@components/prompts/PromptLibraryPanel/PromptLibraryPanel';
import CreateStyleForm from '@features/style/components/CreateStyleForm/CreateStyleForm';
import StyleDetailsPanel from '@features/style/components/StyleDetailsPanel/StyleDetailsPanel';
import Select from '@components/common/Select/Select';
import toast from 'react-hot-toast';
import { extractErrorMessage } from '@utils/helpers';
import { styleUnderstandingText } from '@features/style/utils/styleUnderstanding';
import { buildPromptDownloadMd } from '@utils/promptDefaults';
import { downloadBlob } from '@utils/helpers';
import {
  DOCUMENT_SOURCE_TYPE_OPTIONS,
  DEFAULT_DOCUMENT_SOURCE_TYPE,
} from '@utils/documentRegistry';
import styles from './StylePage.module.scss';

const TABS = ['Style Management', 'Document Registry'];

function documentPreviewText(preview) {
  if (!preview) return '';
  return preview.content ?? preview.text ?? preview.full_content ?? '';
}

export default function StylePage() {
  const dispatch    = useAppDispatch();
  const stylesList  = useAppSelector(selectStyles);
  const documents   = useAppSelector(selectDocuments);
  const documentStats = useAppSelector(selectDocumentStats);
  const activeStyle = useAppSelector(selectActiveStyle);
  const isLoading   = useAppSelector(selectStyleLoading);
  const generatingStyleId = useAppSelector(selectGeneratingStyleId);
  const isUploadingDoc = useAppSelector((s) => s.style.isUploadingDoc);
  const error       = useAppSelector(selectStyleError);
  const selCluster  = useAppSelector(selectSelectedCluster);
  const canModify   = useAppSelector(selectIsReviewer);
  const isAdmin     = useAppSelector(selectIsAdmin);
  const clusterPromptApiReady = isClusterPromptApiAvailable();
  const [clusterPrompts, setClusterPrompts] = useState([]);

  useEffect(() => {
    if (!clusterPromptApiReady || !selCluster?.id) {
      setClusterPrompts([]);
      return;
    }
    clusterPromptService.listForCluster(selCluster.id)
      .then((res) => setClusterPrompts(res.items || []))
      .catch(() => setClusterPrompts([]));
  }, [clusterPromptApiReady, selCluster?.id]);

  const [activeTab, setActiveTab]         = useState(0);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [docTag, setDocTag]               = useState(DEFAULT_DOCUMENT_SOURCE_TYPE);
  const [docPreviewId, setDocPreviewId] = useState(null);
  const [docPreview, setDocPreview] = useState(null);
  const [docPreviewLoading, setDocPreviewLoading] = useState(false);
  const [deleteDocId, setDeleteDocId] = useState(null);

  const [viewStyleId, setViewStyleId] = useState(null);
  const [viewStyle, setViewStyle] = useState(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [refineStyle, setRefineStyle] = useState(null); // { id, name, excerpt }
  const [refineCorrections, setRefineCorrections] = useState('');
  const [refineLoading, setRefineLoading] = useState(false);
  const [filesStyleId, setFilesStyleId] = useState(null);
  const [filesStyleName, setFilesStyleName] = useState('');
  const [filesToUpload, setFilesToUpload] = useState([]);
  const [selectedLibDocIds, setSelectedLibDocIds] = useState([]);
  const [filesExtraInstructions, setFilesExtraInstructions] = useState('');
  const [linkedDocIds, setLinkedDocIds] = useState(() => new Set());
  const [modalLibraryDocs, setModalLibraryDocs] = useState([]);
  const [filesModalLoading, setFilesModalLoading] = useState(false);
  const [deleteStyleId, setDeleteStyleId] = useState(null);
  const [scopeStyleId, setScopeStyleId] = useState(null);
  const [scopeStyleName, setScopeStyleName] = useState('');

  // Document Registry UI (Streamlit-like)
  const [docsHelpOpen, setDocsHelpOpen] = useState(true);
  const [docsUploadOpen, setDocsUploadOpen] = useState(false);
  const [docTypeFilter, setDocTypeFilter] = useState('all');
  const [docStatusFilter, setDocStatusFilter] = useState('all');
  const [docSearch, setDocSearch] = useState('');
  const [docPage, setDocPage] = useState(1);
  const DOCS_PAGE_SIZE = 8;

  useEffect(() => {
    dispatch(fetchStylesThunk());
    dispatch(fetchDocumentsThunk());
  }, []);

  async function onUploadDocs() {
    if (!uploadedFiles.length) return;
    const existingNames = new Set((documents || []).map((d) => (d.name || '').toLowerCase()));
    const duplicates = uploadedFiles.filter((f) => existingNames.has(f.name.toLowerCase()));
    if (duplicates.length) {
      toast.error(`'${duplicates[0].name}' already exists. Use Update action below.`);
      return;
    }
    await dispatch(uploadDocumentsThunk({ files: uploadedFiles, sourceType: docTag }));
    setUploadedFiles([]);
    setDocTag(DEFAULT_DOCUMENT_SOURCE_TYPE);
  }

  async function openView(styleId) {
    setViewStyleId(styleId);
    setViewStyle(null);
    setViewLoading(true);
    try {
      const full = await styleService.getStyle(styleId);
      setViewStyle(full);
    } catch (e) {
      toast.error(extractErrorMessage(e));
      setViewStyleId(null);
    } finally {
      setViewLoading(false);
    }
  }

  async function refreshViewStyle(styleId) {
    if (viewStyleId !== styleId) return;
    try {
      const full = await styleService.getStyle(styleId);
      setViewStyle(full);
    } catch {
      /* keep existing */
    }
  }

  const UNDERSTANDING_EXCERPT_LEN = 400;

  async function openRefine(styleId, styleName) {
    setRefineLoading(true);
    setRefineCorrections('');
    setRefineStyle({ id: styleId, name: styleName, excerpt: '' });
    try {
      const full = await styleService.getStyle(styleId);
      const text = styleUnderstandingText(full);
      if (!text) {
        toast.error('Generate an initial understanding first (click Understand).');
        setRefineStyle(null);
        return;
      }
      const excerpt = text.length > UNDERSTANDING_EXCERPT_LEN
        ? `${text.slice(0, UNDERSTANDING_EXCERPT_LEN)}…`
        : text;
      setRefineStyle({ id: styleId, name: full.name || styleName, excerpt });
    } catch (e) {
      toast.error(extractErrorMessage(e));
      setRefineStyle(null);
    } finally {
      setRefineLoading(false);
    }
  }

  async function onRefineSubmit() {
    if (!refineStyle?.id) return;
    if (!refineCorrections.trim()) {
      toast.error('Please enter corrections or guidance.');
      return;
    }
    const result = await dispatch(refineStyleThunk({
      styleId: refineStyle.id,
      corrections: refineCorrections.trim(),
    }));
    if (!result.error) {
      setRefineStyle(null);
      setRefineCorrections('');
      await refreshViewStyle(refineStyle.id);
    }
  }

  async function openAddFiles(styleId, styleName) {
    setFilesStyleId(styleId);
    setFilesStyleName(styleName);
    setFilesToUpload([]);
    setSelectedLibDocIds([]);
    setFilesExtraInstructions('');
    setFilesModalLoading(true);
    try {
      const [full, libDocs] = await Promise.all([
        styleService.getStyle(styleId),
        styleService.listAllDocuments(),
      ]);
      setLinkedDocIds(new Set((full.reference_documents || []).map((d) => d.id)));
      setModalLibraryDocs(libDocs);
    } catch (e) {
      toast.error(extractErrorMessage(e));
      setFilesStyleId(null);
    } finally {
      setFilesModalLoading(false);
    }
  }

  function closeAddFilesModal() {
    setFilesStyleId(null);
    setFilesStyleName('');
    setFilesToUpload([]);
    setSelectedLibDocIds([]);
    setFilesExtraInstructions('');
    setLinkedDocIds(new Set());
    setModalLibraryDocs([]);
  }

  const availableLibDocs = (modalLibraryDocs.length ? modalLibraryDocs : documents || [])
    .filter((d) => !linkedDocIds.has(d.id));

  const librarySelectOptions = availableLibDocs.map((d) => ({
    value: d.id,
    label: d.name || `Document #${d.id}`,
  }));

  async function onAppendStyleFiles() {
    if (!filesStyleId) return;
    if (selectedLibDocIds.length === 0 && filesToUpload.length === 0) {
      toast.error('No new files selected or uploaded.');
      return;
    }
    const styleId = filesStyleId;
    const result = await dispatch(uploadStyleDocsThunk({
      styleId,
      files: filesToUpload,
      documentIds: selectedLibDocIds,
      additionalInstructions: filesExtraInstructions,
    }));
    if (!result.error) {
      closeAddFilesModal();
      await refreshViewStyle(styleId);
    }
  }

  async function onUnderstandStyle(styleId) {
    const result = await dispatch(regenerateStyleThunk(styleId));
    if (!result.error) {
      await refreshViewStyle(styleId);
    }
  }

  function openScopePicker(styleId, styleName) {
    setScopeStyleId(styleId);
    setScopeStyleName(styleName);
  }

  async function onActivateScope(scope) {
    if (!scopeStyleId) return;
    const result = await dispatch(activateStyleThunk({ styleId: scopeStyleId, scope }));
    if (!result.error) {
      setScopeStyleId(null);
      setScopeStyleName('');
    }
  }

  function handleDownloadUnderstandPrompt() {
    const md = buildPromptDownloadMd({
      component: 'style',
      promptName: 'style_understanding',
      promptVersion: 'active',
      systemPrompt: '',
      userPromptTemplate: '',
      extraInstructions: '',
    });
    downloadBlob(new Blob([md], { type: 'application/msword' }), 'prompt_style_understanding.doc');
  }

  async function openDocPreview(documentId) {
    setDocPreviewId(documentId);
    setDocPreview(null);
    setDocPreviewLoading(true);
    try {
      const content = await styleService.getDocumentContent(documentId);
      setDocPreview(content);
    } catch (e) {
      toast.error(extractErrorMessage(e));
      setDocPreviewId(null);
    } finally {
      setDocPreviewLoading(false);
    }
  }

  const docTypes = documentStats?.distinctTags?.length
    ? documentStats.distinctTags
    : Array.from(new Set((documents || []).map((d) => (d.source_type || 'reference').toLowerCase()))).sort();
  const typeOptions = [{ value: 'all', label: 'All' }, ...docTypes.map((t) => ({ value: t, label: t }))];
  const statusOptions = [{ value: 'all', label: 'All' }, { value: 'active', label: 'active' }, { value: 'archived', label: 'archived' }];

  const registryKpis = {
    total: documentStats?.total ?? documents?.length ?? 0,
    active: documentStats?.active ?? documents?.length ?? 0,
    archived: documentStats?.archived ?? 0,
    types: documentStats?.types ?? docTypes.length,
  };

  // If a previously selected filter no longer exists, reset to All (prevents empty lists).
  useEffect(() => {
    if (docTypeFilter !== 'all' && !docTypes.includes(docTypeFilter)) {
      setDocTypeFilter('all');
    }
  }, [docTypes, docTypeFilter]);

  const filteredDocs = (documents || [])
    .filter((d) => (docTypeFilter === 'all' ? true : (d.source_type || 'reference').toLowerCase() === docTypeFilter))
    .filter((d) => {
      if (docStatusFilter === 'all') return true;
      if (docStatusFilter === 'active') return true;
      return false;
    })
    .filter((d) => {
      const q = docSearch.trim().toLowerCase();
      if (!q) return true;
      return (d.name || '').toLowerCase().includes(q);
    });

  const totalDocs = filteredDocs.length;
  const totalPages = Math.max(1, Math.ceil(totalDocs / DOCS_PAGE_SIZE));
  const safePage = Math.min(docPage, totalPages);
  const pageDocs = filteredDocs.slice((safePage - 1) * DOCS_PAGE_SIZE, safePage * DOCS_PAGE_SIZE);

  return (
    <PageContainer title="Style Management" breadcrumbs={[{ label: 'Style' }]}>
      {selCluster?.id && clusterPromptApiReady && (
        <div className={styles.clusterInjected} role="note">
          <div className={styles.clusterInjected__title}>⚡ Auto-Injected Cluster Prompts</div>
          <p className={styles.clusterInjected__desc}>
            These prompts are inherited from this cluster and automatically prepended
            to the Style context for every course here.
          </p>
          {clusterPrompts.length === 0 ? (
            <p className={styles.clusterEmpty}>No cluster prompts assigned to this cluster yet.</p>
          ) : (
            <ul className={styles.clusterInjected__list}>
              {clusterPrompts.map((p) => (
                <li key={p.id} className={styles.clusterInjected__item}>
                  <strong>{p.name}</strong>
                  {p.description && <span> — {p.description}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!activeStyle && (
        <div className={styles.noActiveBanner} role="status">
          ⚠️ No active style. Create and activate a Style below for consistent tone and structure across all generations.
        </div>
      )}
      {/* Tab Bar */}
      <div className={styles.tabs} role="tablist">
        {TABS.map((tab, i) => (
          <button
            key={i}
            role="tab"
            aria-selected={activeTab === i}
            className={`${styles.tab} ${activeTab === i ? styles['tab--active'] : ''}`}
            onClick={() => setActiveTab(i)}
            type="button"
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Style Management Tab */}
      {activeTab === 0 && (
        <div className={styles.layout}>
          {!canModify && (
            <div className={styles.readOnlyBanner} role="status">
              🔒 <strong>Read-only access.</strong> Only Admins and Leads can create or modify styles.
            </div>
          )}
          {error && (
            <ErrorState message={error} onRetry={() => dispatch(fetchStylesThunk())} />
          )}
          <CreateStyleForm />

          {/* Saved Styles — Streamlit right panel */}
          <section className={`${styles.panel} ${styles.panelLibrary}`}>
            <h2 className={styles.panel__title}>🗂️ Saved Styles</h2>
            {activeStyle && (
              <div className={styles.activeIndicator}>
                <span aria-hidden="true">🎨</span> Active: <strong>{activeStyle.name}</strong>
              </div>
            )}
            {isLoading ? (
              <div className={styles.center}><Loader size="lg" /></div>
            ) : stylesList.length === 0 ? (
              <EmptyState
                title="No styles yet"
                message="Create your first style using the form on the left."
              />
            ) : (
              <ul className={styles.list}>
                {stylesList.map((style) => (
                  <li key={style.id} className={`${styles.styleItem} ${style.is_active ? styles['styleItem--active'] : ''}`}>
                    <div className={styles.styleItem__info}>
                      <span className={styles.styleItem__name}>{style.name}</span>
                      {style.understanding_preview && (
                        <span className={styles.styleItem__desc}>{style.understanding_preview}</span>
                      )}
                      <span className={styles.styleItem__date}>{formatDate(style.created_at)}</span>
                    </div>
                    <div className={styles.styleItem__actions}>
                      <Button
                        variant="ghost"
                        size="xs"
                        className={styles.actionBtn}
                        onClick={() => openView(style.id)}
                      >
                        View
                      </Button>
                      {canModify && (
                        <>
                          <Button
                            variant="ghost"
                            size="xs"
                            className={styles.actionBtn}
                            onClick={() => onUnderstandStyle(style.id)}
                            loading={generatingStyleId === style.id}
                          >
                            Understand
                          </Button>
                          <Button
                            variant="ghost"
                            size="xs"
                            className={styles.actionBtn}
                            onClick={() => openRefine(style.id, style.name)}
                            disabled={generatingStyleId != null}
                          >
                            Refine
                          </Button>
                          <Button
                            variant="ghost"
                            size="xs"
                            className={styles.actionBtn}
                            onClick={() => openAddFiles(style.id, style.name)}
                          >
                            + Files
                          </Button>
                          {style.is_active ? (
                            <Button variant="primary" size="xs" className={styles.actionBtn} onClick={() => dispatch(deactivateStyleThunk(style.id))}>Deactivate</Button>
                          ) : (
                            <Button variant="primary" size="xs" className={styles.actionBtn} onClick={() => openScopePicker(style.id, style.name)}>Activate</Button>
                          )}
                          {isAdmin && (
                            <Button variant="danger" size="xs" className={styles.actionBtn} onClick={() => setDeleteStyleId(style.id)}>Delete</Button>
                          )}
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className={styles.panelFull}>
            <PromptLibraryPanel component="style" />
          </div>
        </div>
      )}

      {/* Document Registry Tab */}
      {activeTab === 1 && (
        <div className={styles.docRegistry}>
          {/* How-to / quick reference */}
          <section className={styles.docHelp}>
            <button type="button" className={styles.docHelp__toggle} onClick={() => setDocsHelpOpen((v) => !v)}>
              💡 How to reference documents in prompts {docsHelpOpen ? '▾' : '▸'}
            </button>
            {docsHelpOpen && (
              <div className={styles.docHelp__body}>
                <p><strong>Document-Aware Generation</strong> — mention any uploaded filename in backticks inside your topic or prompt.</p>
                <p><strong>Examples:</strong></p>
                <ul className={styles.quickCard__list}>
                  <li><code>`Instructional_Design_Spec_v2.pdf`</code> — Generate a lesson summary based on this file</li>
                  <li><code>`Course_Outline.docx`</code> — Use this as reference material for the module</li>
                </ul>
                <p>The system will detect the filename, retrieve the content, and inject it as structured context.</p>
              </div>
            )}
          </section>

          {/* KPIs */}
          <section className={styles.kpiRow}>
            <div className={styles.kpi}><div className={styles.kpi__label}>📄 Total</div><div className={styles.kpi__value}>{registryKpis.total}</div></div>
            <div className={styles.kpi}><div className={styles.kpi__label}>✅ Active</div><div className={styles.kpi__value}>{registryKpis.active}</div></div>
            <div className={styles.kpi}><div className={styles.kpi__label}>🗄️ Archived</div><div className={styles.kpi__value}>{registryKpis.archived}</div></div>
            <div className={styles.kpi}><div className={styles.kpi__label}>🏷️ Types</div><div className={styles.kpi__value}>{registryKpis.types}</div></div>
          </section>

          {/* Upload expander */}
          <section className={styles.docUpload}>
            <button type="button" className={styles.docUpload__toggle} onClick={() => setDocsUploadOpen((v) => !v)}>
              ⬆️ Upload New Document {docsUploadOpen ? '▾' : '▸'}
            </button>
            {docsUploadOpen && (
              <div className={styles.docUpload__body}>
                <FileUpload
                  accept=".pdf,.docx,.txt,.xlsx"
                  multiple
                  onChange={setUploadedFiles}
                  label="Drop files here or click to browse"
                  hint="Supported: PDF, DOCX, TXT, XLSX"
                />
                {uploadedFiles.length > 0 && (
                  <div className={styles.fileList}>
                    {uploadedFiles.map((f, i) => (
                      <span key={i} className={styles.fileTag}>{f.name}</span>
                    ))}
                  </div>
                )}
                <Select
                  label="Document Type"
                  options={DOCUMENT_SOURCE_TYPE_OPTIONS}
                  value={docTag}
                  onChange={(e) => setDocTag(e.target.value)}
                />
                <Button variant="primary" onClick={onUploadDocs} disabled={uploadedFiles.length === 0} loading={isUploadingDoc}>
                  📁 Add to Database
                </Button>
              </div>
            )}
          </section>

          {/* Registry header + filters */}
          <section className={styles.docHeader}>
            <h2 className={styles.docHeader__title}>🗂️ Document Registry</h2>
            <div className={styles.docFilters}>
              <Select
                label="Filter by Type"
                options={typeOptions}
                value={docTypeFilter}
                onChange={(e) => { setDocTypeFilter(e.target.value); setDocPage(1); }}
              />
              <Select
                label="Filter by Status"
                options={statusOptions}
                value={docStatusFilter}
                onChange={(e) => { setDocStatusFilter(e.target.value); setDocPage(1); }}
              />
              <div className={styles.docSearch}>
                <label className={styles.docSearch__label}>Search filename</label>
                <SearchBar value={docSearch} onChange={(v) => { setDocSearch(v); setDocPage(1); }} placeholder="e.g. Spec_v2" />
              </div>
            </div>

            <div className={styles.docPager}>
              <Button variant="ghost" size="sm" disabled={safePage <= 1} onClick={() => setDocPage((p) => Math.max(1, p - 1))}>
                ← Prev
              </Button>
              <div className={styles.docPager__meta}>Page {safePage} of {totalPages} · {totalDocs} item(s)</div>
              <Button variant="ghost" size="sm" disabled={safePage >= totalPages} onClick={() => setDocPage((p) => Math.min(totalPages, p + 1))}>
                Next →
              </Button>
            </div>
          </section>

          {/* List */}
          <section className={styles.docList}>
            {isLoading ? (
              <div className={styles.center}><Loader size="lg" /></div>
            ) : docStatusFilter === 'archived' ? (
              <EmptyState
                title="No archived documents in list"
                message="The document API returns active documents only. Archived count is shown in the metrics above; individual archived rows require a list-by-status API."
              />
            ) : pageDocs.length === 0 ? (
              <EmptyState title="No documents match your filters" message="Upload a document above or adjust your filters." />
            ) : (
              pageDocs.map((doc) => (
                <article key={doc.id} className={styles.docRow}>
                  <div className={styles.docRow__main}>
                    <div className={styles.docRow__name}>{doc.name}</div>
                    <div className={styles.docRow__meta}>#{doc.id} · {doc.file_type?.toUpperCase() || '—'}</div>
                  </div>
                  <div className={styles.docRow__chips}>
                    <span className={styles.chip}>{(doc.source_type || 'reference').toUpperCase()}</span>
                    <span className={`${styles.chip} ${styles['chip--active']}`}>ACTIVE</span>
                  </div>
                  <div className={styles.docRow__dates}>
                    <div><span className={styles.miniLabel}>Uploaded</span><div>{formatDate(doc.created_at)}</div></div>
                  </div>
                  <div className={styles.docRow__actions}>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => openDocPreview(doc.id)}
                      title="View"
                    >
                      👁️
                    </Button>
                    <Button variant="ghost" size="sm" disabled title="Update (requires document update API)">✏️</Button>
                    <Button variant="ghost" size="sm" onClick={() => setDeleteDocId(doc.id)} title="Archive">🗄️</Button>
                  </div>
                </article>
              ))
            )}
          </section>

          {documents?.length > 0 && (
            <section className={styles.quickRefCard}>
              <div className={styles.quickCard__title}>📋 Quick Reference Card</div>
              <ul className={styles.quickRefList}>
                {(documents || []).slice(0, 100).map((d) => (
                  <li key={d.id}><code>{d.name}</code> — {d.source_type || 'general'}</li>
                ))}
                {registryKpis.active > 100 && (
                  <li>… and {registryKpis.active - 100} more (use filters above to find them)</li>
                )}
              </ul>
            </section>
          )}
        </div>
      )}


      {/* View modal — Streamlit Style Details content parity */}
      <Modal
        open={Boolean(viewStyleId)}
        onClose={() => { setViewStyleId(null); setViewStyle(null); }}
        title={`Style Details${viewStyle?.name ? ` — ${viewStyle.name}` : ''}`}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={handleDownloadUnderstandPrompt}>⬇️ Download Prompt</Button>
            <Button variant="ghost" onClick={() => { setViewStyleId(null); setViewStyle(null); }}>Close</Button>
          </>
        }
      >
        <StyleDetailsPanel
          style={viewStyle}
          loading={viewLoading}
          inModal
          onPreviewDocument={openDocPreview}
        />
      </Modal>

      {/* Refine modal — Streamlit "Refine Style Understanding" panel */}
      <Modal
        open={Boolean(refineStyle)}
        onClose={() => { setRefineStyle(null); setRefineCorrections(''); }}
        title={`Refine Style Understanding${refineStyle?.name ? ` — ${refineStyle.name}` : ''}`}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => { setRefineStyle(null); setRefineCorrections(''); }}>Cancel</Button>
            <Button
              variant="primary"
              loading={generatingStyleId === refineStyle?.id}
              disabled={refineLoading || !refineCorrections.trim()
                || (generatingStyleId != null && generatingStyleId !== refineStyle?.id)}
              onClick={onRefineSubmit}
            >
              Regenerate Understanding
            </Button>
          </>
        }
      >
        {refineLoading ? (
          <Loader size="lg" />
        ) : (
          <div className={styles.refinePanel}>
            <div className={styles.refineBlock}>
              <h3 className={styles.refineBlock__title}>Current Intelligence Layer (excerpt)</h3>
              <p className={styles.refineBlock__caption}>Style Understanding Output</p>
              <div className={styles.refineExcerpt}>{refineStyle?.excerpt || '—'}</div>
            </div>
            <label className={styles.refineLabel} htmlFor="refine-corrections">
              Corrections / additional guidance <span className={styles.refineRequired}>*</span>
            </label>
            <textarea
              id="refine-corrections"
              className={styles.refineTextarea}
              rows={5}
              value={refineCorrections}
              onChange={(e) => setRefineCorrections(e.target.value)}
              placeholder="e.g. The tone should be warmer, not purely academic. Add guidance on scenario-based openings."
            />
          </div>
        )}
      </Modal>

      {/* +Files modal — Streamlit "Add Files to Style" panel */}
      <Modal
        open={Boolean(filesStyleId)}
        onClose={closeAddFilesModal}
        title={`Add Files to Style${filesStyleName ? ` — ${filesStyleName}` : ''}`}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={closeAddFilesModal}>Cancel</Button>
            <Button
              variant="primary"
              disabled={filesModalLoading || (selectedLibDocIds.length === 0 && filesToUpload.length === 0)}
              onClick={onAppendStyleFiles}
            >
              Append Files
            </Button>
          </>
        }
      >
        {filesModalLoading ? (
          <Loader size="lg" />
        ) : (
          <div className={styles.addFilesPanel}>
            <p className={styles.addFilesIntro}>
              Upload additional reference files. They will be appended to the existing file list —
              no existing files will be removed.
            </p>

            <MultiSelect
              label="Add from document library"
              hint="Only documents not already linked to this style are shown."
              placeholder="Choose options"
              options={librarySelectOptions}
              value={selectedLibDocIds}
              onChange={setSelectedLibDocIds}
              disabled={librarySelectOptions.length === 0}
            />
            {librarySelectOptions.length === 0 && (
              <p className={styles.addFilesEmpty}>
                No library documents available to add (all are already linked or the registry is empty).
              </p>
            )}

            <div className={styles.addFilesBlock}>
              <span className={styles.addFilesLabel}>Upload new reference files</span>
              <FileUpload
                accept=".pdf,.docx,.txt"
                multiple
                onChange={setFilesToUpload}
                label="Upload"
                hint="200MB per file · PDF, DOCX, TXT"
              />
            </div>

            <Input
              label="Additional Instructions (optional)"
              placeholder="e.g. Focus more on assessment tone"
              value={filesExtraInstructions}
              onChange={(e) => setFilesExtraInstructions(e.target.value)}
            />
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteStyleId)}
        onClose={() => setDeleteStyleId(null)}
        onConfirm={() => { dispatch(deleteStyleThunk(deleteStyleId)); setDeleteStyleId(null); }}
        title="Delete Style"
        message="Delete this style? This cannot be undone."
        confirmLabel="Delete"
        variant="danger"
      />

      <Modal
        open={Boolean(scopeStyleId)}
        onClose={() => { setScopeStyleId(null); setScopeStyleName(''); }}
        title={`Choose activation scope${scopeStyleName ? ` — ${scopeStyleName}` : ''}`}
        size="sm"
        footer={<Button variant="ghost" onClick={() => { setScopeStyleId(null); setScopeStyleName(''); }}>Cancel</Button>}
      >
        <p className={styles.scopeIntro}>Where should this style be active?</p>
        <div className={styles.scopeActions}>
          <Button variant="primary" fullWidth onClick={() => onActivateScope('course')}>
            For This Course
          </Button>
          <Button variant="secondary" fullWidth onClick={() => onActivateScope('project')}>
            For This Project
          </Button>
          {isAdmin && (
            <Button variant="secondary" fullWidth onClick={() => onActivateScope('global')}>
              Globally
            </Button>
          )}
        </div>
      </Modal>

      <Modal
        open={Boolean(docPreviewId)}
        onClose={() => { setDocPreviewId(null); setDocPreview(null); }}
        title={`Document Preview${docPreview?.name ? ` — ${docPreview.name}` : ''}`}
        size="md"
      >
        {docPreviewLoading ? <Loader size="lg" /> : documentPreviewText(docPreview) ? (
          <>
            <textarea className={styles.viewTextarea} rows={14} readOnly value={documentPreviewText(docPreview).slice(0, 3000)} />
            {docPreview?.name && (
              <p className={styles.docRefHint}>
                💡 Reference in prompt: <code>{`\`${docPreview.name}\``}</code>
              </p>
            )}
          </>
        ) : (
          <p className={styles.viewEmpty}>
            No extracted text for this document. Re-upload the file if content was never parsed.
          </p>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteDocId)}
        onClose={() => setDeleteDocId(null)}
        onConfirm={() => { dispatch(deleteDocumentThunk(deleteDocId)); setDeleteDocId(null); }}
        title="Archive Document"
        message="Archive this document? It will be removed from the active library but retained in the database."
        confirmLabel="Archive"
        variant="danger"
      />

    </PageContainer>
  );
}
