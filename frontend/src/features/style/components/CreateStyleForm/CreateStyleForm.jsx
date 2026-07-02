import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { createStyleThunk, fetchDocumentsThunk } from '@features/style/styleThunks';
import { selectDocuments, selectStyleCreating } from '@features/style/styleSlice';
import { selectIsReviewer } from '@features/auth/authSlice';
import { createStyleSchema } from '@utils/validation';
import Input from '@components/common/Input/Input';
import MultiSelect from '@components/common/MultiSelect/MultiSelect';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Button from '@components/common/Button/Button';
import styles from './CreateStyleForm.module.scss';

const REF_DOCS_HINT = 'These documents define tone, guidelines, and structure for this style.';
const INSTR_HINT = 'These instructions are stored with the style and injected into every generation.';

export default function CreateStyleForm() {
  const dispatch = useAppDispatch();
  const documents = useAppSelector(selectDocuments);
  const isCreating = useAppSelector(selectStyleCreating);
  const canModify = useAppSelector(selectIsReviewer);

  const [selectedLibDocIds, setSelectedLibDocIds] = useState([]);
  const [newFiles, setNewFiles] = useState([]);

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
    dispatch(fetchDocumentsThunk());
  }, [dispatch]);

  const libraryOptions = useMemo(
    () => (documents || []).map((d) => ({
      value: d.id,
      label: d.name || `Document #${d.id}`,
    })),
    [documents],
  );

  async function onSubmit(data) {
    const result = await dispatch(createStyleThunk({
      ...data,
      document_ids: selectedLibDocIds,
      newFiles,
    }));
    if (!result.error) {
      reset();
      setSelectedLibDocIds([]);
      setNewFiles([]);
    }
  }

  if (!canModify) {
    return (
      <section className={styles.panel}>
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
    <section className={styles.panel}>
      <header className={styles.header}>
        <h2 className={styles.header__title}>➕ Create New Style</h2>
        <p className={styles.header__intro}>
          A Style defines writing tone, rules, and structure. It will be automatically
          applied to CDD, Blueprint, and content generation.
        </p>
      </header>

      <form onSubmit={handleSubmit(onSubmit)} className={styles.form}>
        <Input
          label="Style Name"
          required
          placeholder="e.g. Clinical Nursing — Formal Academic"
          error={errors.name?.message}
          {...register('name')}
        />
        <Input
          label="Description"
          placeholder="Brief description of this style's purpose"
          error={errors.description?.message}
          {...register('description')}
        />

        <div className={styles.sectionLabel}>📎 Reference Documents</div>

        <div className={styles.fieldRow}>
          <label className={styles.fieldLabel} htmlFor="style-lib-docs">
            Select existing documents from library
            <span
              className={styles.helpIcon}
              title={REF_DOCS_HINT}
              aria-label={REF_DOCS_HINT}
            >
              ?
            </span>
          </label>
          <MultiSelect
            placeholder="Choose options"
            options={libraryOptions}
            value={selectedLibDocIds}
            onChange={setSelectedLibDocIds}
            disabled={libraryOptions.length === 0}
            hint={libraryOptions.length === 0 ? 'No documents in the library yet.' : undefined}
          />
        </div>

        <div className={styles.uploadBlock}>
          <label className={styles.fieldLabel}>
            Upload new style reference files (multiple allowed)
          </label>
          <div className={styles.uploadBox}>
            <FileUpload
              accept=".pdf,.docx,.txt"
              multiple
              onChange={setNewFiles}
              label="Upload"
              hint="200MB per file · PDF, DOCX, TXT"
            />
          </div>
          {newFiles.length > 0 && (
            <div className={styles.fileTags}>
              {newFiles.map((f, i) => (
                <span key={`${f.name}-${i}`} className={styles.fileTag}>{f.name}</span>
              ))}
            </div>
          )}
        </div>

        <div className={styles.sectionLabel}>✍️ Custom Instructions</div>

        <div className={styles.fieldRow}>
          <label className={styles.fieldLabel} htmlFor="style-custom-instructions">
            Custom instructions / rules for this style
            <span
              className={styles.helpIcon}
              title={INSTR_HINT}
              aria-label={INSTR_HINT}
            >
              ?
            </span>
          </label>
          <textarea
            id="style-custom-instructions"
            className={styles.textarea}
            rows={6}
            placeholder={
              'e.g. Always use active voice. Use Bloom\'s taxonomy levels 3–5. '
              + 'Avoid jargon. Begin each lesson with a real-world clinical scenario.'
            }
            {...register('custom_instructions')}
          />
          {errors.custom_instructions && (
            <p className={styles.fieldError} role="alert">{errors.custom_instructions.message}</p>
          )}
        </div>

        <div className={styles.saveRow}>
          <div />
          <Button
            type="submit"
            variant="primary"
            fullWidth
            loading={isCreating}
            className={styles.saveBtn}
          >
            💾 Save Style
          </Button>
          <div />
        </div>
      </form>
    </section>
  );
}
