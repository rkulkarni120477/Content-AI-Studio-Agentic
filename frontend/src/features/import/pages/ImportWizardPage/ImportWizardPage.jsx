import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
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
import { useLabels } from '@hooks/useLabels';
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
 * Import wizard (reverse pipeline): upload & validate an IMSCC/Cendoc package →
 * start import → watch reconstruction → choose Editor or Feedback Import.
 * Pre-course flow at /projects/:projectId/import (cluster via ?cluster_id=).
 */
export default function ImportWizardPage() {
  const L = useLabels();
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
  // Prefer live wizard courseId; fall back to completed import record.
  const resolvedCourseId = courseId ?? record?.course_id ?? null;

  // Editor modules/blocks are populated by progress 74%; reverse-gen (blueprint/
  // CDD/style) continues after that — enable next-step actions as soon as content exists.
  const CONTENT_READY_AT = 74;
  const stepLabel = job?.current_step || '';
  const contentReady = step === 'done' || (
    step === 'progress'
    && resolvedCourseId
    && (
      (job?.progress ?? 0) >= CONTENT_READY_AT
      || /blueprint|course design|style|finaliz/i.test(stepLabel)
    )
  );

  const [file, setFile] = useState(null);
  const [name, setName] = useState('');

  // Fresh wizard when project/cluster changes (not on every HMR remount of same URL).
  const entryKey = `${pid}:${clusterId ?? ''}`;
  const lastEntryKey = useRef(null);
  useEffect(() => {
    if (lastEntryKey.current === entryKey) return;
    lastEntryKey.current = entryKey;
    dispatch(resetImport());
  }, [dispatch, entryKey]);

  // Prefill the course name from the parsed manifest title (once, if untouched).
  useEffect(() => {
    if (validation?.course_title && !name) setName(validation.course_title);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [validation?.course_title]);

  // On completion, fetch the import record so any warnings can be surfaced.
  useEffect(() => {
    if (step === 'done' && importId) dispatch(fetchImportRecordThunk(importId));
  }, [step, importId, dispatch]);

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

  function handleOpenEditor() {
    if (!resolvedCourseId) {
      toast.error(`${L.title} is not ready yet.`);
      return;
    }
    // Workspace Editor: /workspace/:courseId/editor
    navigate(ROUTES.EDITOR(resolvedCourseId));
  }

  function handleFeedbackImport() {
    if (!resolvedCourseId) {
      toast.error(`${L.title} is not ready yet.`);
      return;
    }
    navigate(ROUTES.FEEDBACK(resolvedCourseId));
  }

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <header className={styles.header}>
          <button type="button" className={styles.back} onClick={() => navigate(coursesRoute)}>
            ← {L.titles}
          </button>
          <h1 className={styles.title}>Import a {L.title}</h1>
          <p className={styles.subtitle}>
            Upload a Canvas IMSCC or Cengage CendocXML package and reconstruct it as an editable CAS {L.titleLower}.
          </p>
        </header>

        {step === 'upload' && (
          <div className={styles.body}>
            <FileUpload
              accept=".imscc,.zip,.xml"
              label={`Drop a ${L.titleLower} package here or click to browse`}
              hint="Canvas IMSCC (.imscc / .zip) or Cengage CendocXML (.zip / .xml)"
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
                {validation.package_format && (
                  <p className={styles.filename}>
                    Detected format:{' '}
                    {validation.package_format === 'cendoc' ? 'CendocXML' : 'Canvas IMSCC'}
                  </p>
                )}
                <Input
                  label={`${L.title} Name *`}
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
                {contentReady
                  ? `${L.title} content is in the Editor. Design artifacts (${L.blueprint} / ${L.cdd} / ${L.style}) are still finishing — you can continue below.`
                  : `Reconstructing modules, pages and quizzes. This can take a moment for large ${L.titlesLower}.`}
              </p>
            </div>

            {contentReady && (
              <div className={styles.done__actions}>
                <Button
                  variant="primary"
                  onClick={handleOpenEditor}
                  disabled={!resolvedCourseId}
                >
                  Go to Editor →
                </Button>
                <Button
                  variant="secondary"
                  onClick={handleFeedbackImport}
                  disabled={!resolvedCourseId}
                >
                  Feedback Import →
                </Button>
              </div>
            )}
          </div>
        )}

        {step === 'done' && (
          <div className={styles.body}>
            <div className={styles.done}>
              <span className={styles.done__icon} aria-hidden="true">✅</span>
              <p className={styles.done__text}>
                {importWarnings.length > 0
                  ? 'Import complete — a few items were flagged for review.'
                  : 'Import complete — your title content is ready.'}
              </p>
              <p className={styles.done__hint}>
                Open the Editor to review lessons, or continue to Reviewer Feedback.
              </p>
              <div className={styles.done__actions}>
                <Button
                  variant="primary"
                  onClick={handleOpenEditor}
                  disabled={!resolvedCourseId}
                >
                  Go to Editor →
                </Button>
                <Button
                  variant="secondary"
                  onClick={handleFeedbackImport}
                  disabled={!resolvedCourseId}
                >
                  Feedback Import →
                </Button>
              </div>
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
              <Button variant="ghost" onClick={() => navigate(coursesRoute)}>Back to {L.titles}</Button>
              <Button variant="primary" onClick={handleRetry}>Try Again</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
