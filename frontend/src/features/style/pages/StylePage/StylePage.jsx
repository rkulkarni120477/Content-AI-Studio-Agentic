import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchStylesThunk, createStyleThunk, activateStyleThunk,
  deactivateStyleThunk, fetchDocumentsThunk, uploadDocumentsThunk,
  regenerateStyleThunk,
} from '@features/style/styleThunks';
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

  const { register, handleSubmit, formState: { errors }, reset } = useForm({
    resolver: zodResolver(createStyleSchema),
  });

  useEffect(() => {
    dispatch(fetchStylesThunk());
    dispatch(fetchDocumentsThunk());
  }, [dispatch]);

  async function onCreateStyle(data) {
    const payload = { ...data, document_ids: selectedDocIds };
    const result = await dispatch(createStyleThunk(payload));
    if (!result.error) { reset(); setSelectedDocIds([]); }
  }

  async function onUploadDocs() {
    if (!uploadedFiles.length) return;
    await dispatch(uploadDocumentsThunk({ files: uploadedFiles, docTag }));
    setUploadedFiles([]);
    setDocTag('');
  }

  const DOC_COLUMNS = [
    { key: 'filename',    header: 'Filename',  sortable: true },
    { key: 'file_type',   header: 'Type' },
    { key: 'doc_tag',     header: 'Tag' },
    { key: 'uploaded_at', header: 'Uploaded',  render: (v) => formatDate(v) },
  ];

  return (
    <PageContainer title="Style Management" breadcrumbs={[{ label: 'Style' }]}>
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
                        {doc.filename}
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
          <section className={styles.panel}>
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
                      {style.is_active ? (
                        <>
                          <span className={styles.badge__active}>Active</span>
                          <Button variant="ghost" size="xs" onClick={() => dispatch(deactivateStyleThunk(style.id))}>Deactivate</Button>
                        </>
                      ) : (
                        <Button variant="ghost" size="xs" onClick={() => dispatch(activateStyleThunk(style.id))}>Activate</Button>
                      )}
                      <Button variant="ghost" size="xs" onClick={() => dispatch(regenerateStyleThunk(style.id))} title="Regenerate AI understanding">
                        ↻
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}

      {/* Document Registry Tab */}
      {activeTab === 1 && (
        <div className={styles.layout}>
          <section className={styles.panel}>
            <h2 className={styles.panel__title}>Upload Documents</h2>
            <div className={styles.form}>
              <FileUpload
                accept=".pdf,.docx,.txt"
                multiple
                onChange={setUploadedFiles}
                label="Drop reference docs here or click to browse"
                hint="Supported: PDF, DOCX, TXT"
              />
              {uploadedFiles.length > 0 && (
                <div className={styles.fileList}>
                  {uploadedFiles.map((f, i) => (
                    <span key={i} className={styles.fileTag}>{f.name}</span>
                  ))}
                </div>
              )}
              <Input label="Document Tag (optional)" value={docTag} onChange={(e) => setDocTag(e.target.value)} placeholder="e.g., brand-guide" />
              <Button variant="primary" onClick={onUploadDocs} disabled={uploadedFiles.length === 0} loading={isGenerating}>
                Upload
              </Button>
            </div>
          </section>
          <section className={styles.panel}>
            <h2 className={styles.panel__title}>Document Registry</h2>
            <Table
              columns={DOC_COLUMNS}
              rows={documents}
              rowKey="id"
              isLoading={isLoading}
              emptyTitle="No documents"
              emptyMessage="Upload reference docs above."
              pagination
              pageSize={20}
            />
          </section>
        </div>
      )}
    </PageContainer>
  );
}
