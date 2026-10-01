import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchActiveChecklistThunk,
  fetchLatestReviewThunk,
  fetchReviewHistoryThunk,
  fetchChecklistResultsThunk,
  fetchFindingsThunk,
  applyFindingThunk,
  applyFindingsThunk,
  dismissFindingThunk,
  startReviewThunk,
} from '@features/review/reviewThunks';
import {
  selectReviewChecklist,
  selectReviewLoaded,
  selectReviewLoading,
  selectReviewRun,
  selectReviewRunning,
  selectChecklistResults,
  selectFindings,
  selectReviewHistory,
  selectReviewError,
  resetReviewRun,
} from '@features/review/reviewSlice';
import { selectModelChoice } from '@features/dashboard/dashboardSlice';
import ChecklistManager from '@features/review/components/ChecklistManager/ChecklistManager';
import Button from '@components/common/Button/Button';
import { useLabels } from '@hooks/useLabels';
import { formatDateTime } from '@utils/helpers';
import styles from './ReviewPanel.module.scss';

/**
 * Page-level "AI Review" section shown above the block cards in the Editor.
 * Step 2 can start a review of the selected lesson and show its run status;
 * findings arrive in later steps. Only rendered when the CE Review flag is on.
 */
export default function ReviewPanel({ generationId = null, projectId = null, onContentChanged }) {
  const dispatch = useAppDispatch();
  const L = useLabels();
  const checklist = useAppSelector(selectReviewChecklist);
  const loaded = useAppSelector(selectReviewLoaded);
  const isLoading = useAppSelector(selectReviewLoading);
  const review = useAppSelector(selectReviewRun);
  const isRunning = useAppSelector(selectReviewRunning);
  const results = useAppSelector(selectChecklistResults);
  const findings = useAppSelector(selectFindings);
  const history = useAppSelector(selectReviewHistory);
  const error = useAppSelector(selectReviewError);
  const modelChoice = useAppSelector(selectModelChoice);

  const [open, setOpen] = useState(true);

  // Load the active checklist for the current project when opened / project changes.
  useEffect(() => {
    if (open) dispatch(fetchActiveChecklistThunk(projectId));
  }, [open, projectId, dispatch]);

  // When the selected lesson changes, drop stale run state and load its latest review + history.
  useEffect(() => {
    dispatch(resetReviewRun());
    if (generationId) {
      dispatch(fetchLatestReviewThunk(generationId));
      dispatch(fetchReviewHistoryThunk(generationId));
    }
  }, [generationId, dispatch]);

  // Load results + findings whenever a completed review is shown.
  useEffect(() => {
    if (review?.run_status !== 'completed') return;
    if (review.review_basis === 'checklist') dispatch(fetchChecklistResultsThunk(review.id));
    dispatch(fetchFindingsThunk(review.id));
  }, [review?.id, review?.run_status, review?.review_basis, dispatch]);

  // Resume tracking a still-running review (e.g. after a page reload) by polling
  // its status until it finishes — so the panel never gets stuck on "Running…" (#10).
  // Bounded (10 min) so a truly stalled job can't poll forever.
  useEffect(() => {
    const active = review?.run_status === 'running' || review?.run_status === 'queued';
    if (!active || isRunning || !generationId) return undefined;
    let polls = 0;
    const t = setInterval(() => {
      if (++polls > 200) { clearInterval(t); return; }   // ~10 min ceiling
      dispatch(fetchLatestReviewThunk(generationId));
    }, 3000);
    return () => clearInterval(t);
  }, [review?.run_status, review?.id, isRunning, generationId, dispatch]);

  async function onStart() {
    if (!generationId || isRunning) return;
    await dispatch(startReviewThunk({ generationId, modelChoice }));
    dispatch(fetchReviewHistoryThunk(generationId));   // refresh history with the new run
  }

  // After content changes, refresh findings + counts and the editor blocks.
  async function refreshAfterChange() {
    if (!review?.id) return;
    await dispatch(fetchFindingsThunk(review.id));
    if (generationId) dispatch(fetchLatestReviewThunk(generationId));
    onContentChanged?.();
  }

  async function onApply(f) {
    await dispatch(applyFindingThunk({ reviewId: review.id, findingId: f.id }));
    await refreshAfterChange();
  }

  async function onApplyAll() {
    await dispatch(applyFindingsThunk({ reviewId: review.id, findingIds: null }));
    await refreshAfterChange();
  }

  async function onDismiss(f) {
    await dispatch(dismissFindingThunk({ reviewId: review.id, findingId: f.id }));
    if (generationId) dispatch(fetchLatestReviewThunk(generationId));   // refresh verdict
  }

  // Headline verdict for a completed review.
  const VERDICT = {
    ready:            { cls: 'v_ready', label: '✅ Ready for Approval' },
    warnings:         { cls: 'v_warn',  label: '⚠️ Passed with warnings — recommendations remain' },
    changes_required: { cls: 'v_block', label: '⛔ Changes Required' },
  };

  const eligibleCount = findings.filter((f) => f.status === 'open' && f.auto_applicable).length;
  const openCount = findings.filter((f) => f.status === 'open').length;
  const dismissedCount = findings.filter((f) => f.status === 'dismissed').length;
  const visibleFindings = findings.filter((f) => f.status !== 'dismissed');   // hide carried dismissals

  const checklistLine = !loaded
    ? (isLoading ? 'Loading…' : '')
    : checklist ? `Checklist: ${checklist.name} (v${checklist.version})` : 'No checklist — style rules will be used';

  return (
    <details
      className={styles.panel}
      open={open}
      // Only the panel's OWN toggle — ignore nested <details> (Manage rules, History)
      // whose toggle event bubbles up in React and would otherwise close the panel.
      onToggle={(e) => { if (e.target === e.currentTarget) setOpen(e.target.open); }}
    >
      <summary className={styles.summary}>
        <span className={styles.title}>🤖 AI Review</span>
        {checklistLine && <span className={styles.status}>— {checklistLine}</span>}
      </summary>
      <div className={styles.body}>
        {/* ── Run controls ─────────────────────────────────────────── */}
        <div className={styles.runRow}>
          <Button
            variant="primary"
            size="sm"
            loading={isRunning}
            disabled={!generationId || isRunning}
            onClick={onStart}
            title={!generationId ? 'Select a file (topic) below first' : undefined}
          >
            {isRunning ? 'Reviewing…' : (review?.run_status === 'completed' ? '🔁 Re-Review' : '▶ Start AI Review')}
          </Button>
          {!generationId && (
            <span className={styles.runHint}>Select a file (topic) below to review it.</span>
          )}
          {review && !isRunning && (
            <span className={`${styles.runBadge} ${styles[`badge_${review.run_status}`] || ''}`}>
              {review.run_status === 'completed' && '✅ Review complete'}
              {review.run_status === 'failed' && '⚠️ Review failed'}
              {review.run_status === 'running' && '🔄 Running…'}
              {review.run_status === 'queued' && '⏳ Queued'}
            </span>
          )}
        </div>

        {error && <div className={styles.error}>⚠️ {error}</div>}

        {review && review.run_status === 'completed' && (
          <div className={styles.result}>
            {/* Headline verdict */}
            {review.verdict && VERDICT[review.verdict] && (
              <div className={`${styles.verdict} ${styles[VERDICT[review.verdict].cls]}`}>
                {VERDICT[review.verdict].label}
                {review.verdict === 'changes_required' && review.counts?.blockers
                  ? ` — ${review.counts.blockers} mandatory rule(s) failed` : ''}
              </div>
            )}
            <div className={styles.resultLine}>
              Reviewed against <strong>{review.review_basis === 'style' ? 'style writing rules' : 'the checklist'}</strong>
              {review.completed_at ? ` · ${formatDateTime(review.completed_at)}` : ''}
            </div>

            {/* Re-review delta + resolved/dismissed summary */}
            {(review.counts?.prev || dismissedCount > 0) && (
              <div className={styles.deltaLine}>
                {review.counts?.prev != null && (
                  <span>Since last review: {review.counts.prev.findings ?? 0} → {openCount} open issue(s). </span>
                )}
                {dismissedCount > 0 && <span>{dismissedCount} dismissed (kept from before).</span>}
              </div>
            )}

            {/* Checklist tally (checklist basis only) */}
            {review.review_basis === 'checklist' && review.counts?.checklist && (
              <div className={styles.tally}>
                <span className={`${styles.tallyChip} ${styles.tally_pass}`}>✓ {review.counts.checklist.pass} pass</span>
                <span className={`${styles.tallyChip} ${styles.tally_fail}`}>✕ {review.counts.checklist.fail} fail</span>
                <span className={`${styles.tallyChip} ${styles.tally_warn}`}>! {review.counts.checklist.warning} warning</span>
                <span className={`${styles.tallyChip} ${styles.tally_na}`}>– {review.counts.checklist.na} n/a</span>
              </div>
            )}

            {/* Non-pass rule details */}
            {review.review_basis === 'checklist' && results.length > 0 && (
              <details className={styles.resultsWrap} open>
                <summary className={styles.resultsSummary}>{results.length} rule(s) need attention</summary>
                <ul className={styles.resultsList}>
                  {results.map((r) => (
                    <li key={r.item_key} className={styles.resultItem}>
                      <span className={`${styles.pill} ${styles[`pill_${r.status}`] || ''}`}>{r.status}</span>
                      <div className={styles.resultText}>
                        <div className={styles.resultRule}>{r.rule_text || r.item_key}</div>
                        {r.explanation && <div className={styles.resultExpl}>{r.explanation}</div>}
                        {r.recommendation && <div className={styles.resultRec}>💡 {r.recommendation}</div>}
                      </div>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {/* Detected issues */}
            {visibleFindings.length > 0 && (
              <details className={styles.resultsWrap} open>
                <summary className={styles.resultsSummary}>
                  {visibleFindings.length} issue(s) found
                  {review.counts?.findings?.more ? ` (+${review.counts.findings.more} more)` : ''}
                </summary>
                {eligibleCount > 0 && (
                  <div className={styles.applyAllRow}>
                    <Button variant="primary" size="xs" onClick={onApplyAll}>
                      ⚡ Apply All Eligible ({eligibleCount})
                    </Button>
                  </div>
                )}
                <ul className={styles.resultsList}>
                  {visibleFindings.map((f) => (
                    <li key={f.id} className={styles.resultItem}>
                      <span className={`${styles.sev} ${styles[`sev_${f.severity}`] || ''}`}>{f.severity}</span>
                      <div className={styles.resultText}>
                        <div className={styles.resultRule}>
                          {f.title || f.category}
                          <span className={styles.cat}>{f.category.replace(/_/g, ' ')}</span>
                          {f.block_label && <span className={styles.loc}>in {f.block_label}</span>}
                          {f.checklist_item_key && <span className={styles.loc}>rule {f.checklist_item_key}</span>}
                          {f.status !== 'open' && <span className={`${styles.fstat} ${styles[`fstat_${f.status}`] || ''}`}>{f.status}</span>}
                        </div>
                        {f.detail && <div className={styles.resultExpl}>{f.detail}</div>}
                        {f.anchor_quote && <div className={styles.quote}>“{f.anchor_quote}”</div>}
                        {f.suggested_replacement && (
                          <div className={styles.replace}>
                            → {f.suggested_replacement}
                            {f.tier === 'large' && <span className={styles.tierTag}>large fix</span>}
                          </div>
                        )}
                        {f.status === 'open' && (
                          <div className={styles.findingActions}>
                            {f.auto_applicable && (
                              <Button variant="secondary" size="xs" onClick={() => onApply(f)}>Apply</Button>
                            )}
                            <Button variant="ghost" size="xs" onClick={() => onDismiss(f)}>Dismiss</Button>
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        {/* ── Review history (§7 audit trail) ──────────────────────── */}
        {history.length > 0 && (
          <details className={styles.manageWrap}>
            <summary className={styles.manageSummary}>🕑 Review history ({history.length})</summary>
            <div className={styles.manageBody}>
              <table className={styles.histTable}>
                <thead>
                  <tr><th>When</th><th>Basis</th><th>Status</th><th>Issues</th><th>By</th></tr>
                </thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.id}>
                      <td>{h.completed_at ? formatDateTime(h.completed_at) : '—'}</td>
                      <td>{h.checklist_version || h.review_basis}</td>
                      <td>{h.verdict || h.run_status}</td>
                      <td>{h.counts?.findings?.total ?? 0}</td>
                      <td>{h.created_by || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}

        {/* ── Manage rules ─────────────────────────────────────────── */}
        <details className={styles.manageWrap}>
          <summary className={styles.manageSummary}>⚙️ Manage checklist rules</summary>
          <div className={styles.manageBody}>
            <p className={styles.note}>
              The CE checklist this {L.titleLower}&rsquo;s content is reviewed against. Rules import
              non-mandatory — tick the ones that must pass before approval.
            </p>
            <ChecklistManager projectId={projectId} />
          </div>
        </details>
      </div>
    </details>
  );
}
