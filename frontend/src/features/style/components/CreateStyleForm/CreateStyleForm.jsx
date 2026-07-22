import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { useParams } from 'react-router-dom';
import { selectSelectedProject, selectSelectedCourse } from '@features/dashboard/dashboardSlice';
import { createStyleThunk } from '@features/style/styleThunks';
import { selectStyleCreating } from '@features/style/styleSlice';
import { selectIsReviewer } from '@features/auth/authSlice';
import { createStyleSchema } from '@utils/validation';
import sourceLibraryApi from '@features/sourceLibrary/services/sourceLibraryApi';
import Input from '@components/common/Input/Input';
import MultiSelect from '@components/common/MultiSelect/MultiSelect';
import Button from '@components/common/Button/Button';
import styles from './CreateStyleForm.module.scss';

const REF_DOCS_HINT = 'All processed DIS Source Library documents are shown here. Select whichever documents should guide this style.';
const INSTR_HINT = 'These instructions are stored with the style and injected into every generation.';

function docLabel(doc) {
  return doc?.source_file_name || doc?.title || `Source ${doc?.job_id || doc?.document_id}`;
}

function styleDocsCacheKey({ courseId, projectId }) {
  return `cas_dis_style_ref_docs:${courseId || projectId || 'global'}`;
}

function readCachedStyleDocs(key) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || 'null');
    return Array.isArray(parsed?.documents) ? parsed.documents : [];
  } catch {
    return [];
  }
}

function writeCachedStyleDocs(key, documents) {
  try {
    window.localStorage.setItem(key, JSON.stringify({ documents, cached_at: new Date().toISOString() }));
  } catch {
    // Ignore storage quota/privacy mode issues.
  }
}

export default function CreateStyleForm({ embedded = false }) {
  const dispatch = useAppDispatch();
  const isCreating = useAppSelector(selectStyleCreating);
  const canModify = useAppSelector(selectIsReviewer);
  const { courseId } = useParams();
  const selectedProject = useAppSelector(selectSelectedProject);
  const selectedCourse = useAppSelector(selectSelectedCourse);

  const [selectedLibDocIds, setSelectedLibDocIds] = useState([]);
  const [sourceDocs, setSourceDocs] = useState([]);
  const [sourceError, setSourceError] = useState('');
  const [sourceLoading, setSourceLoading] = useState(false);
  const sourceCacheKey = styleDocsCacheKey({ courseId: selectedCourse?.id || courseId, projectId: selectedProject?.id });

  const {
    register,
    handleSubmit,
    formState: { errors },
    reset,
  } = useForm({
    resolver: zodResolver(createStyleSchema),
    defaultValues: {
      name: '',
      description: '',
      custom_instructions: '',
    },
  });

  useEffect(() => {
    let cancelled = false;
    async function loadStyleSources() {
      const cached = readCachedStyleDocs(sourceCacheKey);
      if (cached.length && !cancelled) setSourceDocs(cached);
      setSourceLoading(true);
      setSourceError('');
      try {
        // DIS `status` is a review axis (approved_candidate / needs_review), not a
        // processing-complete flag — everything in the source index is already
        // processed. Filtering by status: 'processed' matched nothing and left the
        // selector empty/disabled. List all ingested docs for the course's client.
        const params = {};
        if (selectedCourse?.id || courseId) params.course_id = selectedCourse?.id || courseId;
        else if (selectedProject?.id) params.project_id = selectedProject.id;
        const res = await sourceLibraryApi.listDocuments(params);
        const list = res.documents || res.sources || [];
        if (!cancelled) {
          setSourceDocs(list);
          writeCachedStyleDocs(sourceCacheKey, list);
        }
      } catch (e) {
        if (!cancelled) {
          const cachedAgain = readCachedStyleDocs(sourceCacheKey);
          if (cachedAgain.length) {
            setSourceDocs(cachedAgain);
            setSourceError('Using the last loaded Source Library document list while Source Library refreshes.');
          } else {
            setSourceError('Upload reference documents in Source Library first.');
          }
        }
      } finally {
        if (!cancelled) setSourceLoading(false);
      }
    }
    loadStyleSources();
    const interval = window.setInterval(loadStyleSources, 30000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [selectedCourse?.id, selectedProject?.id, courseId, sourceCacheKey]);

  const libraryOptions = useMemo(
    () => (sourceDocs || []).map((d) => ({
      value: d.job_id || d.document_id,
      label: docLabel(d),
    })),
    [sourceDocs],
  );

  async function onSubmit(data) {
    const result = await dispatch(createStyleThunk({
      ...data,
      document_ids: selectedLibDocIds,
      newFiles: [],
    }));
    if (!result.error) {
      reset();
      setSelectedLibDocIds([]);
    }
  }

  if (!canModify) {
    return (
      <section className={embedded ? styles.panelEmbedded : styles.panel}>
        <div className={styles.restricted}>
          <div className={styles.restricted__icon} aria-hidden="true">🔒</div>
          <div className={styles.restricted__title}>Style Creation Restricted</div>
          <p className={styles.restricted__text}>
            Only Admins and Leads can create or modify Styles. Contact your Lead or Admin
            to update Style configuration.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className={embedded ? styles.panelEmbedded : styles.panel}>
      {!embedded && (
        <header className={styles.header}>
          <h2 className={styles.header__title}>➕ Create New Style</h2>
          <p className={styles.header__intro}>
            A Style defines writing tone, rules, and structure. It will be automatically
            applied to CDD, Blueprint, and content generation.
          </p>
        </header>
      )}
      {embedded && (
        <p className={styles.header__intro}>
          A Style defines writing tone, rules, and structure. Upload source documents in
          Source Library, then select the processed references here.
        </p>
      )}

      <form onSubmit={handleSubmit(onSubmit)} className={styles.form}>
        <Input
          label="Style Name"
          required
          placeholder="e.g. Cengage Authoring Style"
          error={errors.name?.message}
          {...register('name')}
        />
        <Input
          label="Description"
          placeholder="Brief description of this style's purpose"
          error={errors.description?.message}
          {...register('description')}
        />

        <div className={styles.sectionLabel}>📎 DIS Reference Documents</div>

        <div className={styles.fieldRow}>
          <label className={styles.fieldLabel} htmlFor="style-lib-docs">
            Select processed Source Library documents
            <span className={styles.helpIcon} title={REF_DOCS_HINT} aria-label={REF_DOCS_HINT}>?</span>
          </label>
          <MultiSelect
            placeholder={libraryOptions.length ? 'Choose reference documents' : (sourceLoading ? 'Loading Source Library documents…' : 'Choose reference documents')}
            options={libraryOptions}
            value={selectedLibDocIds}
            onChange={setSelectedLibDocIds}
            disabled={!libraryOptions.length}
            hint={sourceError || (!libraryOptions.length ? 'No Style-purpose documents found. Upload them in Source Library with Purpose = Style.' : undefined)}
          />
        </div>

        <p className={styles.fieldHint}>Style documents are managed only in Source Library. This page links processed DIS documents and generates/refines style understanding.</p>

        <div className={styles.sectionLabel}>✍️ Custom Instructions</div>

        <div className={styles.fieldRow}>
          <label className={styles.fieldLabel} htmlFor="style-custom-instructions">
            Custom instructions / rules for this style
            <span className={styles.helpIcon} title={INSTR_HINT} aria-label={INSTR_HINT}>?</span>
          </label>
          <textarea
            id="style-custom-instructions"
            className={styles.textarea}
            rows={6}
            placeholder="e.g. Use concise, learner-facing language. Preserve Cengage authoring terminology. Avoid metadata-heavy output."
            {...register('custom_instructions')}
          />
          {errors.custom_instructions && (
            <p className={styles.fieldError} role="alert">{errors.custom_instructions.message}</p>
          )}
        </div>

        <div className={styles.saveRow}>
          <Button type="submit" variant="primary" size="md" loading={isCreating} className={styles.saveBtn}>
            💾 Save Style
          </Button>
        </div>
      </form>
    </section>
  );
}
