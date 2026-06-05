import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
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
  selectStyles, selectDocuments, selectActiveStyle,
  selectStyleLoading, selectStyleGenerating, selectStyleError,
} from '@features/style/styleSlice';
import { formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import MultiSelect from '@components/common/MultiSelect/MultiSelect';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import SearchBar from '@components/common/SearchBar/SearchBar';
import PromptLibraryPanel from '@components/prompts/PromptLibraryPanel/PromptLibraryPanel';
import CreateStyleForm from '@features/style/components/CreateStyleForm/CreateStyleForm';
import Select from '@components/common/Select/Select';
import toast from 'react-hot-toast';
import { extractErrorMessage } from '@utils/helpers';
import { parseStyleUnderstanding } from '@features/style/utils/styleUnderstanding';
import styles from './StylePage.module.scss';

const TABS = ['Style Management', 'Document Registry'];

function styleUnderstandingText(style) {
  if (!style) return '';
  return style.understanding ?? style.generated_summary ?? '';
}

function styleSlug(style) {
  return style?.style_key ?? style?.style_id ?? '';
}

function documentPreviewText(preview) {
  if (!preview) return '';
  return preview.content ?? preview.text ?? preview.full_content ?? '';
}

export default function StylePage() {
  const dispatch    = useAppDispatch();
  const stylesList  = useAppSelector(selectStyles);
  const documents   = useAppSelector(selectDocuments);
  const activeStyle = useAppSelector(selectActiveStyle);
  const isLoading   = useAppSelector(selectStyleLoading);
  const isGenerating = useAppSelector(selectStyleGenerating);
  const error       = useAppSelector(selectStyleError);

  const [activeTab, setActiveTab]         = useState(0);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [docTag, setDocTag]               = useState('');
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
    await dispatch(uploadDocumentsThunk({ files: uploadedFiles, sourceType: docTag || 'reference' }));
    setUploadedFiles([]);
    setDocTag('');
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
      if (viewStyleId === refineStyle.id) {
        await openView(refineStyle.id);
      }
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
      if (viewStyleId === styleId) {
        await openView(styleId);
      }
    }
  }

  async function onUnderstandStyle(styleId) {
    const result = await dispatch(regenerateStyleThunk(styleId));
    if (!result.error && viewStyleId === styleId) {
      await openView(styleId);
    }
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

  // Streamlit's "Filter by Type" is effectively source_type (reference/style_reference/etc.)
  const docTypes = Array.from(
    new Set((documents || []).map((d) => (d.source_type || 'reference').toLowerCase())),
  ).sort();
  const typeOptions = [{ value: 'all', label: 'All' }, ...docTypes.map((t) => ({ value: t, label: t }))];
  const statusOptions = [{ value: 'all', label: 'All' }, { value: 'active', label: 'Active' }, { value: 'archived', label: 'Archived' }];

  // If a previously selected filter no longer exists, reset to All (prevents empty lists).
  useEffect(() => {
    if (docTypeFilter !== 'all' && !docTypes.includes(docTypeFilter)) {
      setDocTypeFilter('all');
    }
  }, [docTypes, docTypeFilter]);

  const filteredDocs = (documents || [])
    .filter((d) => (docTypeFilter === 'all' ? true : (d.source_type || 'reference').toLowerCase() === docTypeFilter))
    // Backend currently returns only active docs; keep status filter for UI parity.
    .filter(() => (docStatusFilter === 'archived' ? false : true))
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
                      <Button variant="ghost" size="xs" onClick={() => openView(style.id)}>View</Button>
                      <Button variant="ghost" size="xs" onClick={() => onUnderstandStyle(style.id)} loading={isGenerating}>Understand</Button>
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => openRefine(style.id, style.name)}
                        disabled={isGenerating}
                      >
                        Refine
                      </Button>
                      <Button variant="ghost" size="xs" onClick={() => openAddFiles(style.id, style.name)}>
                        + Files
                      </Button>
                      {style.is_active ? (
                        <Button variant="primary" size="xs" onClick={() => dispatch(deactivateStyleThunk(style.id))}>Deactivate</Button>
                      ) : (
                        <Button variant="primary" size="xs" onClick={() => dispatch(activateStyleThunk(style.id))}>Activate</Button>
                      )}
                      <Button variant="danger" size="xs" onClick={() => setDeleteStyleId(style.id)}>Delete</Button>
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
                <p>
                  Documents you upload here are stored in the <strong>Context Document Database</strong>.
                  When generating (CDD / Blueprint / Generate), select documents as “Additional Source Materials”
                  so they’re injected into the prompt context.
                </p>
                <div className={styles.quickCard}>
                  <div className={styles.quickCard__title}>Quick reference</div>
                  <ul className={styles.quickCard__list}>
                    <li><strong>Upload</strong> documents here (PDF/DOCX/TXT/XLSX).</li>
                    <li><strong>Select</strong> documents on Generate / Blueprint as extra context.</li>
                    <li><strong>Keep names clean</strong> so they’re easy to search.</li>
                  </ul>
                </div>
              </div>
            )}
          </section>

          {/* KPIs */}
          <section className={styles.kpiRow}>
            <div className={styles.kpi}><div className={styles.kpi__label}>Total</div><div className={styles.kpi__value}>{documents?.length || 0}</div></div>
            <div className={styles.kpi}><div className={styles.kpi__label}>Active</div><div className={styles.kpi__value}>{documents?.length || 0}</div></div>
            <div className={styles.kpi}><div className={styles.kpi__label}>Archived</div><div className={styles.kpi__value}>0</div></div>
            <div className={styles.kpi}><div className={styles.kpi__label}>Types</div><div className={styles.kpi__value}>{docTypes.length}</div></div>
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
                <Input
                  label="Source Type (optional)"
                  value={docTag}
                  onChange={(e) => setDocTag(e.target.value)}
                  placeholder="e.g., reference, guidelines, chapter"
                />
                <Button variant="primary" onClick={onUploadDocs} disabled={uploadedFiles.length === 0} loading={isGenerating}>
                  Upload
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
            ) : pageDocs.length === 0 ? (
              <EmptyState title="No documents" message="Upload a document above to get started." />
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
                    <Button variant="ghost" size="sm" disabled title="Edit (Streamlit-only for now)">✏️</Button>
                    <Button variant="danger" size="sm" onClick={() => setDeleteDocId(doc.id)} title="Archive/Delete">🗑️</Button>
                  </div>
                </article>
              ))
            )}
          </section>
        </div>
      )}


      {/* View modal — Streamlit Style Details panel parity */}
      <Modal
        open={Boolean(viewStyleId)}
        onClose={() => { setViewStyleId(null); setViewStyle(null); }}
        title={`Style Details${viewStyle?.name ? ` — ${viewStyle.name}` : ''}`}
        size="lg"
        footer={<Button variant="ghost" onClick={() => { setViewStyleId(null); setViewStyle(null); }}>Close</Button>}
      >
        {viewLoading ? (
          <Loader size="lg" />
        ) : viewStyle ? (
          <div className={styles.viewPanel}>
            {viewStyle.description && <p className={styles.viewDesc}>{viewStyle.description}</p>}

            {styleSlug(viewStyle) && (
              <div className={styles.viewMeta}>
                <span className={styles.viewMeta__label}>Style ID</span>
                <code className={styles.viewMeta__code}>{styleSlug(viewStyle)}</code>
              </div>
            )}

            {viewStyle.custom_instructions && (
              <div className={styles.viewBlock}>
                <h3 className={styles.viewBlock__title}>Custom Instructions</h3>
                <textarea
                  className={styles.viewTextarea}
                  rows={4}
                  readOnly
                  value={viewStyle.custom_instructions}
                />
              </div>
            )}

            {(viewStyle.reference_documents?.length > 0) && (
              <div className={styles.viewBlock}>
                <h3 className={styles.viewBlock__title}>
                  Reference Documents ({viewStyle.reference_documents.length})
                </h3>
                <ul className={styles.viewRefList}>
                  {viewStyle.reference_documents.map((doc) => (
                    <li key={doc.id} className={styles.viewRefList__item}>
                      <button
                        type="button"
                        className={styles.viewRefLink}
                        onClick={() => openDocPreview(doc.id)}
                        title="Preview document content"
                      >
                        📄 {doc.name}
                      </button>
                      <span className={styles.viewRefTag}>({doc.source_type || 'general'})</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {viewStyle.understanding_status === 'stale' && (
              <p className={styles.viewStale}>
                Understanding is out of date — new files were added. Click <strong>Understand</strong> to regenerate.
              </p>
            )}

            <div className={styles.viewBlock}>
              <h3 className={styles.viewBlock__title}>🧠 Style Intelligence Layer</h3>
              {(() => {
                const text = styleUnderstandingText(viewStyle);
                if (!text) {
                  return (
                    <p className={styles.viewEmpty}>
                      No understanding generated yet. Click <strong>Understand</strong> to generate.
                    </p>
                  );
                }
                const { sections, rawFallback } = parseStyleUnderstanding(text);
                if (rawFallback) {
                  return (
                    <textarea className={styles.viewTextarea} rows={14} readOnly value={text} />
                  );
                }
                return (
                  <div className={styles.viewSections}>
                    {sections.map((sec) => (
                      <div key={sec.name} className={`${styles.viewSection} ${styles[`viewSection--${sec.tone}`]}`}>
                        <div className={styles.viewSection__head}>
                          {sec.icon} {sec.name}
                        </div>
                        <div className={styles.viewSection__body}>{sec.body}</div>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>
          </div>
        ) : null}
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
              loading={isGenerating}
              disabled={refineLoading || !refineCorrections.trim()}
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

      <Modal open={Boolean(docPreviewId)} onClose={() => { setDocPreviewId(null); setDocPreview(null); }} title={`Document Preview${docPreview?.name ? ` — ${docPreview.name}` : ''}`} size="md">
        {docPreviewLoading ? <Loader size="lg" /> : documentPreviewText(docPreview) ? (
          <textarea className={styles.viewTextarea} rows={14} readOnly value={documentPreviewText(docPreview)} />
        ) : (
          <p className={styles.viewEmpty}>
            No extracted text for this document. Re-upload the file or update it in Streamlit if content was never parsed.
          </p>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteDocId)}
        onClose={() => setDeleteDocId(null)}
        onConfirm={() => { dispatch(deleteDocumentThunk(deleteDocId)); setDeleteDocId(null); }}
        title="Delete Document"
        message="Delete this document from the library?"
        confirmLabel="Delete"
        variant="danger"
      />

    </PageContainer>
  );
}
