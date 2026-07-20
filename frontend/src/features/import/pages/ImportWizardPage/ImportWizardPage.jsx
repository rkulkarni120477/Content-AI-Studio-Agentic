import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  validatePackageThunk,
  startImportThunk,
  fetchImportRecordThunk,
} from '@features/import/importThunks';
import { resetImport, selectImport } from '@features/import/importSlice';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Loader from '@components/common/Loader/Loader';
import { ROUTES } from '@utils/constants';
import styles from './ImportWizardPage.module.scss';

const COUNT_LABELS = [
  ['modules', 'Modules'],
  ['pages', 'Pages'],
  ['quizzes', 'Quizzes'],
  ['assignments', 'Assignments'],
  ['discussions', 'Discussions'],
  ['resources', 'Resources'],
];

/**
 * Import wizard (reverse pipeline): upload & validate an IMSCC → start import →
 * watch reconstruction progress → open the populated Editor. Pre-course flow at
 * /projects/:projectId/import (cluster passed via ?cluster_id=). See reverse_cas.md §S4.
 */
export default function ImportWizardPage() {
  const { projectId } = useParams();
  const [searchParams] = useSearchParams();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();

  const pid = Number(projectId);
  const clusterId = searchParams.get('cluster_id');
  const coursesRoute = clusterId
    ? ROUTES.CLUSTER_COURSES(pid, Number(clusterId))
    : ROUTES.DASHBOARD;

  const {
    step, validating, validation, validateError,
    starting, courseId, importId, job, record, error,
  } = useAppSelector(selectImport);

  const importWarnings = record?.warnings ?? [];

  const [file, setFile] = useState(null);
  const [name, setName] = useState('');

  // Fresh wizard on entry.
  useEffect(() => {
    dispatch(resetImport());
  }, [dispatch]);

  // Prefill the course name from the parsed manifest title (once, if untouched).
  useEffect(() => {
    if (validation?.course_title && !name) setName(validation.course_title);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [validation?.course_title]);

  // On completion, fetch the import record so any warnings can be surfaced.
  useEffect(() => {
    if (step === 'done' && importId) dispatch(fetchImportRecordThunk(importId));
  }, [step, importId, dispatch]);

  // Auto-hand-off to the Editor only when the import is clean; if there are
  // warnings, let the user read them and open the Editor manually.
  useEffect(() => {
    if (step === 'done' && courseId && record && importWarnings.length === 0) {
      const t = setTimeout(() => navigate(ROUTES.EDITOR(courseId)), 900);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [step, courseId, record, importWarnings.length, navigate]);

  function handleFile(files) {
    const picked = files?.[0];
    if (!picked) return;
    setFile(picked);
    dispatch(validatePackageThunk({ file: picked }));
  }

  function handleStart() {
    if (!file || !name.trim()) return;
    dispatch(startImportThunk({ projectId: pid, clusterId, name: name.trim(), file }));
  }

  function handleRetry() {
    dispatch(resetImport());
    setFile(null);
    setName('');
  }

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <header className={styles.header}>
          <button type="button" className={styles.back} onClick={() => navigate(coursesRoute)}>
            ← Courses
          </button>
          <h1 className={styles.title}>Import a Course</h1>
          <p className={styles.subtitle}>
            Upload a Canvas IMSCC package and reconstruct it as an editable CAS course.
          </p>
        </header>

        {step === 'upload' && (
          <div className={styles.body}>
            <FileUpload
              accept=".imscc,.zip"
              label="Drop an IMSCC package here or click to browse"
              hint="Canvas Course Export (.imscc / .zip)"
              onChange={handleFile}
              error={validateError || undefined}
            />

            {file && (
              <p className={styles.filename}>
                <span aria-hidden="true">📦</span> {file.name}
              </p>
            )}

            {validating && (
              <div className={styles.inline}>
                <Loader size="sm" /> <span>Reading package…</span>
              </div>
            )}

            {validateError && (
              <p role="alert" className={styles.error}>{validateError}</p>
            )}

            {validation && (
              <div className={styles.preview}>
                <Input
                  label="Course Name *"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />

                <div className={styles.counts}>
                  {COUNT_LABELS.map(([key, label]) => (
                    <div key={key} className={styles.count}>
                      <span className={styles.count__num}>
                        {validation.structure_counts?.[key] ?? 0}
                      </span>
                      <span className={styles.count__label}>{label}</span>
                    </div>
                  ))}
                </div>

                {validation.warnings?.length > 0 && (
                  <details className={styles.warnings}>
                    <summary>{validation.warnings.length} item(s) flagged for review</summary>
                    <ul>
                      {validation.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}

            <div className={styles.actions}>
              <Button variant="ghost" onClick={() => navigate(coursesRoute)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={handleStart}
                disabled={!file || !name.trim() || validating || Boolean(validateError)}
                loading={starting}
              >
                Start Import
              </Button>
            </div>
          </div>
        )}

        {step === 'progress' && (
          <div className={styles.body}>
            <div className={styles.progress}>
              <div className={styles.progress__bar}>
                <div
                  className={styles.progress__fill}
                  style={{ width: `${job?.progress ?? 0}%` }}
                />
              </div>
              <p className={styles.progress__label}>
                {job?.current_step || 'Queued…'} {job?.progress != null ? `(${job.progress}%)` : ''}
              </p>
              <p className={styles.progress__hint}>
                Reconstructing modules, pages and quizzes. This can take a moment for large courses.
              </p>
            </div>
          </div>
        )}

        {step === 'done' && (
          <div className={styles.body}>
            <div className={styles.done}>
              <span className={styles.done__icon} aria-hidden="true">✅</span>
              <p className={styles.done__text}>
                {importWarnings.length > 0
                  ? 'Import complete — a few items were flagged for review.'
                  : 'Import complete — opening the Editor…'}
              </p>
              {courseId && (
                <Button variant="primary" onClick={() => navigate(ROUTES.EDITOR(courseId))}>
                  Open in Editor →
                </Button>
              )}
            </div>

            {importWarnings.length > 0 && (
              <details className={styles.warnings} open>
                <summary>{importWarnings.length} item(s) flagged for review</summary>
                <ul>
                  {importWarnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        {step === 'error' && (
          <div className={styles.body}>
            <p role="alert" className={styles.error}>{error || 'Something went wrong.'}</p>
            <div className={styles.actions}>
              <Button variant="ghost" onClick={() => navigate(coursesRoute)}>Back to Courses</Button>
              <Button variant="primary" onClick={handleRetry}>Try Again</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
