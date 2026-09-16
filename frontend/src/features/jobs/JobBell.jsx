import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  selectVisibleJobs,
  selectJobBellCount,
  selectActiveJobs,
  dismissJob,
  dismissAllTerminal,
} from '@features/jobs/jobsSlice';
import { jobTypeMeta, jobHref } from '@features/jobs/jobLabels';
import { isTerminalJobStatus, JOB_STATUSES } from '@utils/constants';
import { useLabels } from '@hooks/useLabels';
import { applyTerminology } from '@config/tenantLabels';
import { cn } from '@utils/helpers';
import styles from './JobBell.module.scss';

function statusLabel(job) {
  if (job.status === JOB_STATUSES.COMPLETED) return 'Complete';
  if (job.status === JOB_STATUSES.FAILED) return 'Failed';
  if (job.status === JOB_STATUSES.CANCELLED) return 'Cancelled';
  if (job.status === JOB_STATUSES.QUEUED) return 'Queued';
  return job.currentStep || 'Running';
}

export default function JobBell({ className }) {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const L = useLabels();
  const jobs = useAppSelector(selectVisibleJobs);
  const active = useAppSelector(selectActiveJobs);
  const count = useAppSelector(selectJobBellCount);
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className={cn(styles.bell, className)} ref={rootRef}>
      <button
        type="button"
        className={cn(styles.trigger, active.length > 0 && styles['trigger--active'])}
        aria-label={count ? `${count} background jobs` : 'Background jobs'}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.icon} aria-hidden>🔔</span>
        {count > 0 && (
          <span className={styles.badge}>{count > 9 ? '9+' : count}</span>
        )}
      </button>

      {open && (
        <div className={styles.dropdown} role="menu">
          <div className={styles.header}>
            <strong>Activity</strong>
            {jobs.some((j) => isTerminalJobStatus(j.status)) && (
              <button
                type="button"
                className={styles.clearBtn}
                onClick={() => dispatch(dismissAllTerminal())}
              >
                Clear done
              </button>
            )}
          </div>

          {jobs.length === 0 ? (
            <div className={styles.empty}>No jobs running</div>
          ) : (
            <ul className={styles.list}>
              {jobs.map((job) => {
                const meta = jobTypeMeta(job.jobType);
                const title = applyTerminology(meta.label, L);
                const href = jobHref(job);
                const terminal = isTerminalJobStatus(job.status);
                return (
                  <li
                    key={job.jobId}
                    className={cn(
                      styles.row,
                      job.status === JOB_STATUSES.FAILED && styles['row--failed'],
                      job.status === JOB_STATUSES.COMPLETED && styles['row--done'],
                    )}
                  >
                    <div className={styles.rowMain}>
                      <div className={styles.rowTitle}>
                        {title}
                        {job.block ? ` · ${job.block}` : ''}
                      </div>
                      <div className={styles.rowMeta}>
                        {statusLabel(job)}
                        {!terminal && typeof job.progress === 'number' ? ` · ${job.progress}%` : ''}
                      </div>
                      {job.status === JOB_STATUSES.FAILED && job.errorMessage && (
                        <div className={styles.rowError}>{job.errorMessage}</div>
                      )}
                      {!terminal && (
                        <div className={styles.progressTrack} aria-hidden>
                          <div
                            className={styles.progressFill}
                            style={{ width: `${Math.min(100, Math.max(0, job.progress || 0))}%` }}
                          />
                        </div>
                      )}
                    </div>
                    <div className={styles.rowActions}>
                      {href && (
                        <button
                          type="button"
                          className={styles.linkBtn}
                          onClick={() => {
                            setOpen(false);
                            navigate(href);
                          }}
                        >
                          View
                        </button>
                      )}
                      {terminal && (
                        <button
                          type="button"
                          className={styles.linkBtn}
                          onClick={() => dispatch(dismissJob(job.jobId))}
                        >
                          Dismiss
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
