import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchStylesThunk, createStyleThunk, activateStyleThunk,
  deactivateStyleThunk, fetchDocumentsThunk, uploadDocumentsThunk,
  regenerateStyleThunk,
  deleteStyleThunk,
  updateStyleThunk,
  uploadStyleDocsThunk,
} from '@features/style/styleThunks';
import { deleteDocumentThunk } from '@features/style/styleThunks';
import {
  selectStyles, selectDocuments, selectActiveStyle,
  selectStyleLoading, selectStyleGenerating, selectStyleError,
} from '@features/style/styleSlice';
import { createStyleSchema } from '@utils/validation';
import { formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Table from '@components/common/Table/Table';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import SearchBar from '@components/common/SearchBar/SearchBar';
import {
  selectPrompts, selectPromptVersions, selectAiGenerated,
  selectPromptsLoading, selectAiGenerating,
} from '@features/prompts/promptsSlice';
import {
  fetchPromptsThunk, commitPromptThunk, fetchPromptVersionsThunk, aiGeneratePromptThunk,
} from '@features/prompts/promptsThunks';
import Select from '@components/common/Select/Select';
import styles from './StylePage.module.scss';

const TABS = ['Style Management', 'Document Registry'];

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
  const [selectedDocIds, setSelectedDocIds] = useState([]);
  const [docPreviewId, setDocPreviewId] = useState(null);
  const [docPreview, setDocPreview] = useState(null);
  const [docPreviewLoading, setDocPreviewLoading] = useState(false);
  const [deleteDocId, setDeleteDocId] = useState(null);

  const [viewStyleId, setViewStyleId] = useState(null);
  const [viewStyle, setViewStyle] = useState(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [editStyle, setEditStyle] = useState(null); // { id, name, description }
  const [filesStyleId, setFilesStyleId] = useState(null);
  const [filesToUpload, setFilesToUpload] = useState([]);
  const [deleteStyleId, setDeleteStyleId] = useState(null);

  // Document Registry UI (Streamlit-like)
  const [docsHelpOpen, setDocsHelpOpen] = useState(true);
  const [docsUploadOpen, setDocsUploadOpen] = useState(false);
  const [docTypeFilter, setDocTypeFilter] = useState('all');
  const [docStatusFilter, setDocStatusFilter] = useState('all');
  const [docSearch, setDocSearch] = useState('');
  const [docPage, setDocPage] = useState(1);
  const DOCS_PAGE_SIZE = 8;

  // Prompt Library state (reuse prompts feature)
  const prompts = useAppSelector(selectPrompts);
  const versions = useAppSelector(selectPromptVersions);
  const aiGen = useAppSelector(selectAiGenerated);
  const promptsLoading = useAppSelector(selectPromptsLoading);
  const promptsAiLoading = useAppSelector(selectAiGenerating);
  const [showPromptVersions, setShowPromptVersions] = useState(false);
  const [selectedPrompt, setSelectedPrompt] = useState(null);
  const [aiDescription, setAiDescription] = useState('');
  const promptForm = useForm({ defaultValues: { name: '', component_type: '', description: '', system_prompt: '', user_prompt_template: '', tags: '' } });

  const { register, handleSubmit, formState: { errors }, reset } = useForm({
    resolver: zodResolver(createStyleSchema),
  });

  useEffect(() => {
    dispatch(fetchStylesThunk());
    dispatch(fetchDocumentsThunk());
  }, [dispatch]);

  useEffect(() => {
    // Streamlit shows Prompt Library within Style Management
    if (activeTab === 0) dispatch(fetchPromptsThunk({ component: 'style' }));
  }, [dispatch, activeTab]);

  async function onCreateStyle(data) {
    const payload = { ...data, document_ids: selectedDocIds };
    const result = await dispatch(createStyleThunk(payload));
    if (!result.error) { reset(); setSelectedDocIds([]); }
  }

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
    } finally {
      setViewLoading(false);
    }
  }

  const DOC_COLUMNS = [
    { key: 'name',        header: 'Filename',  sortable: true },
    { key: 'file_type',   header: 'Type' },
    { key: 'source_type', header: 'Source' },
    { key: 'created_at',  header: 'Uploaded',  render: (v) => formatDate(v) },
    {
      key: '__actions',
      header: '',
      render: (_, row) => (
        <div className={styles.rowActions}>
          <Button
            variant="ghost"
            size="xs"
            onClick={async () => {
              setDocPreviewId(row.id);
              setDocPreview(null);
              setDocPreviewLoading(true);
              try {
                const content = await styleService.getDocumentContent(row.id);
                setDocPreview(content);
              } finally {
                setDocPreviewLoading(false);
              }
            }}
          >
            View
          </Button>
          <Button variant="danger" size="xs" onClick={() => setDeleteDocId(row.id)}>
            Delete
          </Button>
        </div>
      ),
    },
  ];

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
          {/* Create Form */}
          <section className={styles.panel}>
            <h2 className={styles.panel__title}>Create New Style</h2>
            <form onSubmit={handleSubmit(onCreateStyle)} className={styles.form}>
              <Input label="Style Name" required error={errors.name?.message} {...register('name')} />
              <Input label="Description" {...register('description')} />
              <div className={styles.form__field}>
                <label className={styles.form__label}>Custom Instructions</label>
                <textarea className={styles.form__textarea} rows={5} {...register('custom_instructions')} placeholder="e.g., Use active voice. Avoid jargon…" />
              </div>

              {/* Multi-select documents */}
              {documents.length > 0 && (
                <div className={styles.form__field}>
                  <label className={styles.form__label}>Attach Reference Documents</label>
                  <div className={styles.docPicker}>
                    {documents.map((doc) => (
                      <label key={doc.id} className={styles.docPicker__item}>
                        <input
                          type="checkbox"
                          checked={selectedDocIds.includes(doc.id)}
                          onChange={(e) => {
                            setSelectedDocIds((prev) =>
                              e.target.checked ? [...prev, doc.id] : prev.filter((id) => id !== doc.id),
                            );
                          }}
                        />
                        {doc.name}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <Button type="submit" variant="primary" fullWidth loading={isGenerating}>
                {isGenerating ? 'Generating AI Understanding…' : '✨ Create Style'}
              </Button>
            </form>
          </section>

          {/* Style Library */}
          <section className={`${styles.panel} ${styles.panelLibrary}`}>
            <h2 className={styles.panel__title}>Style Library</h2>
            {activeStyle && (
              <div className={styles.activeIndicator}>
                <span aria-hidden="true">🎨</span> Active: <strong>{activeStyle.name}</strong>
              </div>
            )}
            {isLoading ? (
              <div className={styles.center}><Loader size="lg" /></div>
            ) : stylesList.length === 0 ? (
              <EmptyState title="No styles" message="Create your first instructional style." />
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
                      <Button variant="ghost" size="xs" onClick={() => dispatch(regenerateStyleThunk(style.id))}>Understand</Button>
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => setEditStyle({ id: style.id, name: style.name, description: style.description || '' })}
                      >
                        Refine
                      </Button>
                      <Button variant="ghost" size="xs" onClick={() => { setFilesStyleId(style.id); setFilesToUpload([]); }}>
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

          {/* Prompt Library (inside Style Management, like Streamlit) */}
          <section className={`${styles.panel} ${styles.panelFull}`}>
            <h2 className={styles.panel__title}>Prompt Library</h2>
            <p className={styles.hintText}>
              Create, edit, version, and improve prompt assets tagged <code>style</code>.
            </p>
            <div className={styles.promptLayout}>
              <div className={styles.promptLeft}>
                <div className={styles.aiBox}>
                  <p className={styles.aiBox__label}>Improve with AI:</p>
                  <textarea
                    className={styles.aiBox__input}
                    rows={2}
                    placeholder="Describe what this prompt should do…"
                    value={aiDescription}
                    onChange={(e) => setAiDescription(e.target.value)}
                  />
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={promptsAiLoading}
                    onClick={() => dispatch(aiGeneratePromptThunk(aiDescription))}
                  >
                    Generate Prompt
                  </Button>
                  {aiGen && (
                    <div className={styles.aiGenResult}>
                      <pre className={styles.aiGenResult__code}>{JSON.stringify(aiGen, null, 2)}</pre>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          promptForm.setValue('system_prompt', aiGen.system_prompt || '');
                          promptForm.setValue('user_prompt_template', aiGen.user_prompt_template || '');
                          promptForm.setValue('component_type', 'style');
                        }}
                      >
                        Use This
                      </Button>
                    </div>
                  )}
                </div>

                <form
                  onSubmit={promptForm.handleSubmit((data) => dispatch(commitPromptThunk({ ...data, component_type: 'style' })))}
                  className={styles.form}
                >
                  <Input label="Asset Name" required placeholder="e.g., default_style_prompt" {...promptForm.register('name', { required: true })} />
                  <Input label="Description" {...promptForm.register('description')} />
                  <div className={styles.form__field}>
                    <label className={styles.form__label}>System Prompt *</label>
                    <textarea className={styles.form__textarea} rows={5} {...promptForm.register('system_prompt', { required: true })} />
                  </div>
                  <div className={styles.form__field}>
                    <label className={styles.form__label}>User Prompt Template *</label>
                    <textarea className={styles.form__textarea} rows={5} {...promptForm.register('user_prompt_template', { required: true })} />
                  </div>
                  <Input label="Tags (comma-separated)" {...promptForm.register('tags')} />
                  <Button type="submit" variant="primary" fullWidth loading={promptsLoading}>
                    Save Prompt Asset
                  </Button>
                </form>
              </div>

              <div className={styles.promptRight}>
                <Table
                  columns={[
                    { key: 'name', header: 'Name', sortable: true },
                    { key: 'description', header: 'Description', render: (v) => v || '—' },
                    {
                      key: '__actions',
                      header: '',
                      render: (_, row) => (
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => {
                            setSelectedPrompt(row);
                            dispatch(fetchPromptVersionsThunk(row.id));
                            setShowPromptVersions(true);
                          }}
                        >
                          Versions
                        </Button>
                      ),
                    },
                  ]}
                  rows={(prompts || []).filter((p) => p.component_type === 'style')}
                  rowKey="name"
                  isLoading={promptsLoading}
                  pagination
                  pageSize={20}
                  emptyTitle="No style prompts yet"
                  emptyMessage="Commit your first style prompt asset."
                />
              </div>
            </div>
          </section>
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
                      onClick={async () => {
                        setDocPreviewId(doc.id);
                        setDocPreview(null);
                        setDocPreviewLoading(true);
                        try {
                          const content = await styleService.getDocumentContent(doc.id);
                          setDocPreview(content);
                        } finally {
                          setDocPreviewLoading(false);
                        }
                      }}
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


      {/* View modal */}
      <Modal
        open={Boolean(viewStyleId)}
        onClose={() => { setViewStyleId(null); setViewStyle(null); }}
        title={`Style Details${viewStyle?.name ? ` — ${viewStyle.name}` : ''}`}
        size="md"
        footer={<Button variant="ghost" onClick={() => { setViewStyleId(null); setViewStyle(null); }}>Close</Button>}
      >
        {viewLoading ? (
          <Loader size="lg" />
        ) : (
          <>
            {viewStyle?.description && <p className={styles.viewDesc}>{viewStyle.description}</p>}
            <textarea className={styles.viewTextarea} rows={12} readOnly value={viewStyle?.understanding || ''} />
          </>
        )}
      </Modal>

      {/* Refine modal */}
      <Modal
        open={Boolean(editStyle)}
        onClose={() => setEditStyle(null)}
        title={`Edit Style — ${editStyle?.name || ''}`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditStyle(null)}>Cancel</Button>
            <Button
              variant="primary"
              onClick={() => {
                dispatch(updateStyleThunk({
                  styleId: editStyle.id,
                  data: { name: editStyle.name, description: editStyle.description || null },
                }));
                setEditStyle(null);
              }}
            >
              Save
            </Button>
          </>
        }
      >
        <Input label="Style Name" value={editStyle?.name || ''} onChange={(e) => setEditStyle((s) => ({ ...s, name: e.target.value }))} />
        <Input label="Description" value={editStyle?.description || ''} onChange={(e) => setEditStyle((s) => ({ ...s, description: e.target.value }))} />
      </Modal>

      {/* +Files modal */}
      <Modal
        open={Boolean(filesStyleId)}
        onClose={() => { setFilesStyleId(null); setFilesToUpload([]); }}
        title="Add Files to Style"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => { setFilesStyleId(null); setFilesToUpload([]); }}>Cancel</Button>
            <Button
              variant="primary"
              disabled={filesToUpload.length === 0}
              onClick={() => {
                dispatch(uploadStyleDocsThunk({ styleId: filesStyleId, files: filesToUpload }));
                setFilesStyleId(null);
                setFilesToUpload([]);
              }}
            >
              Upload
            </Button>
          </>
        }
      >
        <FileUpload
          accept=".pdf,.docx,.txt,.xlsx"
          multiple
          onChange={setFilesToUpload}
          label="Drop style files here or click to browse"
          hint="Supported: PDF, DOCX, TXT, XLSX"
        />
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
        {docPreviewLoading ? <Loader size="lg" /> : (
          <textarea className={styles.viewTextarea} rows={14} readOnly value={docPreview?.content || ''} />
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

      <Modal open={showPromptVersions} onClose={() => setShowPromptVersions(false)} title={`Versions — ${selectedPrompt?.name || ''}`} size="md">
        {versions.length === 0 ? (
          <p className={styles.emptyText}>No versions found.</p>
        ) : (
          <ul className={styles.versionList}>
            {versions.map((v) => (
              <li key={v.id} className={styles.versionItem}>
                <div>
                  <span className={styles.versionItem__ver}>v{v.version}</span>
                  {v.is_active && <span className={styles.badge__active}>Active</span>}
                  <span className={styles.versionItem__date}>{formatDate(v.created_at)}</span>
                  {v.change_reason && <p className={styles.versionItem__reason}>{v.change_reason}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Modal>
    </PageContainer>
  );
}
