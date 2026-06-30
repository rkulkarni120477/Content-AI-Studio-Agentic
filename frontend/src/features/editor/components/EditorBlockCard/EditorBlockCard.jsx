import { useEffect, useMemo, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  updateBlockThunk,
  autosaveBlockThunk,
  regenerateBlockThunk,
  regenerateBlockItemThunk,
  submitBlockThunk,
  triggerPlagiarismThunk,
  fetchPlagiarismStatusThunk,
  restoreBlockVersionThunk,
  createSnapshotThunk,
  scoreBlockThunk,
  fetchGenerationBlocksThunk,
} from '@features/editor/editorThunks';
import { selectPlagiarismByBlock } from '@features/editor/editorSlice';
import { selectModelChoice } from '@features/dashboard/dashboardSlice';
import { editorService } from '@features/editor/services/editorService';
import {
  WORKFLOW_STATES,
  WORKFLOW_EXPORTABLE,
  FEEDBACK_SCOPES,
  FEEDBACK_SCOPE_OPTIONS,
  FEEDBACK_SCOPE_HINTS,
} from '@utils/constants';
import { parseItemsFromSection } from '@utils/blockItems';
import { renderInlineMarkdown } from '@utils/markdownPreview';
import { formatDateTime } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import WorkflowStatusBadge from '@features/editor/components/WorkflowStatusBadge/WorkflowStatusBadge';
import MarkdownPreview from '@features/editor/components/MarkdownPreview/MarkdownPreview';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import styles from './EditorBlockCard.module.scss';

const DEFERRED_AI_REVIEW = 'Deferred for faster UX';

const AUTOSAVE_MS = 10000;

function evalColor(score) {
  if (score > 80) return '#10b981';
  if (score > 50) return '#f59e0b';
  return '#ef4444';
}

function plagColor(sim) {
  if (sim > 70) return '#ef4444';
  if (sim > 30) return '#f59e0b';
  return '#10b981';
}

function ScopeHint({ scope }) {
  const h = FEEDBACK_SCOPE_HINTS[scope];
  if (!h) return null;
  return (
    <div className={styles.scopeHint} style={{ background: h.bg, color: h.fg, borderColor: `${h.fg}33` }}>
      {h.icon} <strong>{h.title}</strong> — {h.tip}
    </div>
  );
}

function ScopeRadios({ value, onChange, name }) {
  return (
    <div className={styles.scopeRadios} role="radiogroup" aria-label="Feedback scope">
      {FEEDBACK_SCOPE_OPTIONS.map((opt) => (
        <label key={opt.id} className={styles.scopeRadios__label}>
          <input
            type="radio"
            name={name}
            value={opt.id}
            checked={value === opt.id}
            onChange={() => onChange(opt.id)}
          />
          {opt.label}
        </label>
      ))}
    </div>
  );
}

export default function EditorBlockCard({ block, generationId, genCreatedBy, onBlockUpdated }) {
  const dispatch = useAppDispatch();
  const { user, isAdmin, isReviewer } = useAuth();
  const modelChoice = useAppSelector(selectModelChoice);
  const plagiarismReport = useAppSelector((s) => selectPlagiarismByBlock(s)[block.id]);

  const [content, setContent] = useState(block.content || '');
  const [editReason, setEditReason] = useState('');
  const [editScope, setEditScope] = useState(FEEDBACK_SCOPES.ONE_TIME);
  const [improveInstr, setImproveInstr] = useState('');
  const [regenScope, setRegenScope] = useState(FEEDBACK_SCOPES.ONE_TIME);
  const [itemInstr, setItemInstr] = useState('');
  const [itemScope, setItemScope] = useState(FEEDBACK_SCOPES.ONE_TIME);
  const [reviewers, setReviewers] = useState([]);
  const [selectedReviewer, setSelectedReviewer] = useState('');
  const [revScore, setRevScore] = useState(3);
  const [revApproved, setRevApproved] = useState(false);
  const [revComments, setRevComments] = useState('');
  const [scoreResult, setScoreResult] = useState(null);
  const [aiReviewText, setAiReviewText] = useState(null);
  const [requestingReview, setRequestingReview] = useState(false);
  const [versions, setVersions] = useState([]);
  const [restoreVerId, setRestoreVerId] = useState('');
  const [snapNote, setSnapNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [itemRegenIdx, setItemRegenIdx] = useState(null);
  const [showDraftRecovery, setShowDraftRecovery] = useState(false);
  const [autosaveStatus, setAutosaveStatus] = useState('saved');
  const [dashboardOpen, setDashboardOpen] = useState(true);
  const [itemPanelOpen, setItemPanelOpen] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);

  const autosaveTimer = useRef(null);
  const lastEditAt = useRef(null);

  const changed = content.trim() !== (block.content || '').trim();
  const canEdit = [WORKFLOW_STATES.DRAFT, WORKFLOW_STATES.CHANGES_REQUESTED, WORKFLOW_STATES.REJECTED]
    .includes(block.workflow_state);
  const canRegen = block.workflow_state === WORKFLOW_STATES.DRAFT;
  const autosaveLocked = WORKFLOW_EXPORTABLE.includes(block.workflow_state);
  const canSubmit = canEdit && (isAdmin || isReviewer || genCreatedBy === user?.username);

  const displayAiReview = block.ai_review || aiReviewText;
  const isDeferredReview = !displayAiReview
    || String(displayAiReview).includes(DEFERRED_AI_REVIEW);

  const parsedItems = useMemo(() => parseItemsFromSection(content), [content]);

  const sources = useMemo(() => {
    if (!block.sources) return [];
    try {
      const list = JSON.parse(block.sources);
      return Array.isArray(list) ? list : [];
    } catch {
      return [];
    }
  }, [block.sources]);

  const evalReport = useMemo(() => {
    if (!block.eval_report) return null;
    try { return JSON.parse(block.eval_report); } catch { return null; }
  }, [block.eval_report]);

  useEffect(() => {
    setContent(block.content || '');
    setShowDraftRecovery(Boolean(block.draft_content && block.draft_content !== block.content));
  }, [block.id, block.content, block.draft_content]);

  useEffect(() => {
    editorService.listReviewers().then((list) => {
      const arr = Array.isArray(list) ? list : (list?.items || []);
      const names = arr.map((u) => (typeof u === 'string' ? u : u.username)).filter(Boolean);
      setReviewers(names);
      if (names[0]) setSelectedReviewer(names[0]);
    }).catch(() => setReviewers([]));
  }, []);

  useEffect(() => {
    if (!changed || !canEdit || autosaveLocked) return undefined;
    lastEditAt.current = Date.now();
    setAutosaveStatus('dirty');
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(async () => {
      await dispatch(autosaveBlockThunk({ blockId: block.id, content }));
      setAutosaveStatus('autosaved');
    }, AUTOSAVE_MS);
    return () => clearTimeout(autosaveTimer.current);
  }, [content, changed, canEdit, autosaveLocked, block.id, dispatch]);

  useEffect(() => {
    if (!plagiarismReport?.report_id) return undefined;
    if (!['pending', 'processing'].includes(plagiarismReport.status)) return undefined;
    const t = setInterval(() => {
      dispatch(fetchPlagiarismStatusThunk({
        blockId: block.id,
        reportId: plagiarismReport.report_id,
      }));
    }, 3000);
    return () => clearInterval(t);
  }, [plagiarismReport, block.id, dispatch]);

  async function refreshBlocks() {
    if (generationId) await dispatch(fetchGenerationBlocksThunk(generationId));
    onBlockUpdated?.();
  }

  async function loadVersions() {
    const list = await editorService.getBlockVersions(block.id);
    setVersions(list || []);
  }

  useEffect(() => {
    if (versionOpen) loadVersions();
  }, [versionOpen, block.id]);

  async function onSave() {
    setSaving(true);
    try {
      await dispatch(updateBlockThunk({
        blockId: block.id,
        data: { content, change_reason: editReason || 'Manual edit' },
      })).unwrap();
      setAutosaveStatus('saved');
      setShowDraftRecovery(false);
      await refreshBlocks();
    } finally {
      setSaving(false);
    }
  }

  async function onRegenerate() {
    setRegenerating(true);
    try {
      await dispatch(regenerateBlockThunk({
        blockId: block.id,
        instruction: improveInstr,
        modelChoice,
      })).unwrap();
      await refreshBlocks();
    } finally {
      setRegenerating(false);
    }
  }

  async function onRegenItem(idx) {
    setItemRegenIdx(idx);
    try {
      const res = await dispatch(regenerateBlockItemThunk({
        blockId: block.id,
        itemIndex: idx,
        sectionKey: block.block_label || 'content',
        feedback: itemInstr,
        modelChoice,
      })).unwrap();
      if (res?.updated_content) setContent(res.updated_content);
      await refreshBlocks();
    } finally {
      setItemRegenIdx(null);
    }
  }

  async function onPlagiarism() {
    const res = await dispatch(triggerPlagiarismThunk(block.id)).unwrap();
    if (res?.report_id) {
      dispatch(fetchPlagiarismStatusThunk({ blockId: block.id, reportId: res.report_id }));
    }
  }

  const plag = plagiarismReport;
  const simScore = plag?.similarity_score;

  return (
    <article className={styles.block}>
      <h3 className={styles.blockTitle}>
        <WorkflowStatusBadge state={block.workflow_state} />
        {' '}{block.block_label}
        <em className={styles.blockTitle__id}> (Block #{block.id})</em>
      </h3>

      <details className={styles.stExpander} open={dashboardOpen} onToggle={(e) => setDashboardOpen(e.target.open)}>
        <summary className={styles.stExpander__summary}>📊 Plagiarism & Citation Dashboard</summary>
        <div className={styles.stExpander__body}>
        <div className={styles.dashboard__grid}>
          <div className={styles.dashboard__col}>
            <strong>🛡️ Plagiarism (Copyleaks)</strong>
            {!plag && (
              <>
                <p className={styles.caption}>Not checked yet.</p>
                <Button variant="secondary" size="sm" fullWidth onClick={onPlagiarism}>🔍 Check Plagiarism</Button>
              </>
            )}
            {plag?.status === 'pending' && (
              <>
                <p className={styles.info}>⏳ Scan queued…</p>
                <Button variant="ghost" size="sm" fullWidth onClick={() => dispatch(fetchPlagiarismStatusThunk({ blockId: block.id, reportId: plag.report_id }))}>🔄 Refresh</Button>
              </>
            )}
            {plag?.status === 'processing' && (
              <>
                <p className={styles.info}>🔄 Checking via Copyleaks…</p>
                <Button variant="ghost" size="sm" fullWidth onClick={() => dispatch(fetchPlagiarismStatusThunk({ blockId: block.id, reportId: plag.report_id }))}>🔄 Refresh</Button>
              </>
            )}
            {plag?.status === 'completed' && simScore != null && (
              <>
                <p className={styles.scoreBig} style={{ color: plagColor(simScore) }}>{simScore.toFixed(1)}%</p>
                <p className={styles.caption}>Similarity (Copyleaks)</p>
                {plag.ai_score != null && (
                  <p className={styles.aiScore} style={{ color: plagColor(plag.ai_score) }}>
                    🤖 AI score: <strong>{plag.ai_score.toFixed(1)}%</strong>
                  </p>
                )}
                <Button variant="ghost" size="sm" fullWidth onClick={onPlagiarism}>🔁 Re-check</Button>
              </>
            )}
            {plag?.status === 'failed' && (
              <>
                <div className={styles.alertWarn}>
                  ⚠️ Scan failed{plag.error_message ? `: ${plag.error_message}` : ''}
                </div>
                <Button variant="ghost" size="sm" fullWidth onClick={onPlagiarism}>🔁 Reset & Retry</Button>
              </>
            )}
          </div>
          <div className={styles.dashboard__col}>
            <strong>📚 Citations</strong>
            {!plag ? (
              <p className={styles.caption}>Run plagiarism check to view source citations.</p>
            ) : sources.length > 0 ? (
              <ul className={styles.citeList}>
                {sources.map((s, i) => <li key={i}><code>{s}</code></li>)}
              </ul>
            ) : (
              <p className={styles.caption}>No sources cited.</p>
            )}
          </div>
          <div className={styles.dashboard__col}>
            <strong>📐 AI Evaluation</strong>
            {block.eval_score != null ? (
              <>
                <p className={styles.scoreBig} style={{ color: evalColor(block.eval_score) }}>
                  {block.eval_score}/100
                </p>
                {evalReport?.missing_sections?.length > 0 && (
                  <p className={styles.warn}>Missing: {evalReport.missing_sections.join(', ')}</p>
                )}
              </>
            ) : (
              <p className={styles.caption}>No evaluation data yet.</p>
            )}
          </div>
        </div>
        <hr className={styles.divider} />
        <strong>👁️ Expert AI Review</strong>
        {isDeferredReview ? (
          <p className={styles.caption}>
            Deferred for faster UX. Use <strong>Request AI Review</strong> from the Editor when needed.
          </p>
        ) : (
          <div className={styles.aiReview}>{displayAiReview}</div>
        )}
        </div>
      </details>

      <div className={styles.columns}>
        <div className={styles.editCol}>
          {showDraftRecovery && block.draft_content && (
            <div className={styles.draftBanner}>
              <strong>📂 Unsaved draft found</strong>
              <p>A draft was autosaved before your last session ended. Restore it to continue where you left off, or dismiss to keep the current saved version.</p>
              <div className={styles.draftBanner__actions}>
                <Button variant="secondary" size="sm" onClick={() => { setContent(block.draft_content); setShowDraftRecovery(false); }}>📂 Restore Draft</Button>
                <Button variant="ghost" size="sm" onClick={() => setShowDraftRecovery(false)}>✕ Dismiss</Button>
              </div>
            </div>
          )}

          <label className={styles.fieldLabel}>✏️ Edit Block (Markdown)</label>
          <textarea
            className={styles.textarea}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            disabled={!canEdit || autosaveLocked}
            rows={16}
          />

          {changed && canEdit && (
            <>
              <div className={styles.editHint}>
                ✏️ <strong>You&apos;ve edited this block.</strong> Optionally describe why and choose how to use this feedback.
              </div>
              <input
                className={styles.inlineInput}
                placeholder="Why did you make this edit? (optional)"
                value={editReason}
                onChange={(e) => setEditReason(e.target.value)}
              />
              <ScopeRadios value={editScope} onChange={setEditScope} name={`edit-scope-${block.id}`} />
              <ScopeHint scope={editScope} />
            </>
          )}

          <Button variant="primary" size="sm" fullWidth loading={saving} disabled={!canEdit || autosaveLocked} onClick={onSave}>
            💾 Save Edit
          </Button>

          <p className={styles.autosaveStatus}>
            {autosaveLocked && '⚠️ Autosave paused — content is Approved/Published. Reset to Draft before editing.'}
            {!autosaveLocked && autosaveStatus === 'dirty' && '⏳ Unsaved changes…'}
            {!autosaveLocked && autosaveStatus === 'autosaved' && '✅ Autosaved just now'}
            {!autosaveLocked && autosaveStatus === 'saved' && !changed && '✅ All changes saved'}
          </p>

          {canSubmit && (
            <div className={styles.submitSection}>
              <div className={styles.submitSection__header}>📤 Submit for Review</div>
              <div className={styles.submitSection__card}>
                {reviewers.length > 0 ? (
                  <>
                    <select
                      className={styles.nativeSelect}
                      value={selectedReviewer}
                      onChange={(e) => setSelectedReviewer(e.target.value)}
                    >
                      {reviewers.map((u) => (
                        <option key={u} value={u}>{u}</option>
                      ))}
                    </select>
                    <Button
                      variant="ghost"
                      size="sm"
                      fullWidth
                      disabled={!selectedReviewer}
                      onClick={() => dispatch(submitBlockThunk({
                        blockId: block.id,
                        action: 'submit',
                        data: { reviewer_username: selectedReviewer },
                      })).then(refreshBlocks)}
                    >
                      📤 Submit for Review
                    </Button>
                  </>
                ) : (
                  <p className={styles.caption}>No reviewers available — ask an Admin to add reviewers.</p>
                )}
              </div>
            </div>
          )}

          <div className={styles.regenBanner}>
            <span className={styles.regenBanner__icon}>✨</span>
            <span className={styles.regenBanner__title}>Regenerate / Improvise Prompt</span>
            <span className={styles.regenBanner__sub}>— Add instructions below to guide the AI</span>
          </div>

          <hr className={styles.divider} />

          <p className={styles.regenLabel}>🔄 Regenerate / Improvise</p>
          {!canRegen && (
            <p className={styles.caption}>ℹ️ Move this block back to Draft in Workflow to regenerate.</p>
          )}

          {parsedItems.length > 0 && (
            <details className={styles.stExpander} open={itemPanelOpen} onToggle={(e) => setItemPanelOpen(e.target.open)}>
              <summary className={styles.stExpander__summary}>🎯 Regenerate a single item ({parsedItems.length} items found)</summary>
              <div className={styles.stExpander__body}>
              <p className={styles.caption}>
                Click ⟳ next to any item to regenerate <strong>only that item</strong>. All siblings are preserved exactly.
              </p>
              <input
                className={styles.inlineInput}
                placeholder="e.g. Make more specific, add a real-world example"
                value={itemInstr}
                onChange={(e) => setItemInstr(e.target.value)}
              />
              <ScopeRadios value={itemScope} onChange={setItemScope} name={`item-scope-${block.id}`} />
              <ul className={styles.itemList}>
                {parsedItems.map((item, idx) => (
                  <li key={idx} className={styles.itemList__row}>
                    <span className={styles.itemList__text}>
                      <code>{idx + 1}</code>
                      <span
                        className={styles.itemList__md}
                        dangerouslySetInnerHTML={{ __html: renderInlineMarkdown(item.text) }}
                      />
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={itemRegenIdx === idx}
                      disabled={!canRegen}
                      onClick={() => onRegenItem(idx)}
                      title={`Regenerate item ${idx + 1} only`}
                    >
                      ⟳
                    </Button>
                  </li>
                ))}
              </ul>
              </div>
            </details>
          )}

          <p className={styles.regenOr}>— or regenerate the full block below —</p>

          <textarea
            className={styles.regenInstr}
            placeholder="e.g. Improve flow, add real-world examples, simplify for beginners, rewrite as a quiz."
            value={improveInstr}
            onChange={(e) => setImproveInstr(e.target.value)}
            disabled={!canRegen}
            rows={3}
          />
          <ScopeRadios value={regenScope} onChange={setRegenScope} name={`regen-scope-${block.id}`} />
          <ScopeHint scope={regenScope} />
          <button
            type="button"
            className={styles.streamlitPrimary}
            disabled={!canRegen || regenerating}
            onClick={onRegenerate}
          >
            {regenerating ? 'Regenerating…' : '🔄 Regenerate'}
          </button>
        </div>

        <div className={styles.previewCol}>
          <p className={styles.previewCaption}>Live Preview</p>
          <MarkdownPreview content={content} />
        </div>
      </div>

      <details className={styles.stExpander} open={reviewOpen} onToggle={(e) => setReviewOpen(e.target.open)}>
        <summary className={styles.stExpander__summary}>📝 Reviewer Comment (Block #{block.id})</summary>
        <div className={styles.stExpander__body}>
        <div className={styles.reviewCols}>
          <div>
            <p className={styles.reviewSub}>📝 Submit Formal Review</p>
            <div className={styles.sliderWrap}>
              <span className={styles.sliderValue}>{revScore}</span>
              <input type="range" min={1} max={5} value={revScore} onChange={(e) => setRevScore(Number(e.target.value))} className={styles.range} />
            </div>
            <label className={styles.checkLabel}>
              <input type="checkbox" checked={revApproved} onChange={(e) => setRevApproved(e.target.checked)} />
              Approve this block
            </label>
            <textarea
              className={styles.regenInstr}
              placeholder="Describe strengths, weaknesses, and suggestions…"
              value={revComments}
              onChange={(e) => setRevComments(e.target.value)}
              rows={4}
            />
            <Button variant="ghost" size="sm" fullWidth onClick={() => editorService.rateBlock(block.id, revScore).then(refreshBlocks)}>
              ✅ Submit Review
            </Button>
          </div>
          <div>
            <p className={styles.reviewSub}>🎯 AI Quality Analysis</p>
            <Button variant="ghost" size="sm" fullWidth onClick={async () => {
              const r = await dispatch(scoreBlockThunk(block.id)).unwrap();
              setScoreResult(r);
            }}>
              🤖 Run AI Evaluation
            </Button>
            {scoreResult && (
              <div className={styles.scorePanel}>
                <p><strong>Overall Grade:</strong> {scoreResult.grade || 'N/A'} — {scoreResult.score ?? scoreResult.total_score}/100</p>
                {scoreResult.feedback && <p className={styles.caption}>💡 {scoreResult.feedback}</p>}
              </div>
            )}
            <Button
              variant="ghost"
              size="sm"
              fullWidth
              loading={requestingReview}
              style={{ marginTop: 8 }}
              onClick={async () => {
                setRequestingReview(true);
                try {
                  const r = await dispatch(scoreBlockThunk(block.id)).unwrap();
                  setAiReviewText(r.feedback || r.metadata?.review || 'Review complete.');
                } finally {
                  setRequestingReview(false);
                }
              }}
            >
              📝 Request AI Review
            </Button>
          </div>
        </div>
        </div>
      </details>

      <details className={styles.stExpander} open={versionOpen} onToggle={(e) => setVersionOpen(e.target.open)}>
        <summary className={styles.stExpander__summary}>⏱️ Version History — Block #{block.id}</summary>
        <div className={styles.stExpander__body}>
        {versions.length === 0 ? (
          <p className={styles.caption}>
            No snapshots yet. Click Save Snapshot below to capture the current content, or use Regenerate to auto-save.
          </p>
        ) : (
          <>
            <table className={styles.versionTable}>
              <thead>
                <tr>
                  <th>v#</th><th>When</th><th>By</th><th>Source</th><th>Words</th><th>State</th><th>Note</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version_id}>
                    <td>{v.version_number || v.version_id}</td>
                    <td>{v.created_at ? formatDateTime(v.created_at) : '—'}</td>
                    <td>{v.created_by || '—'}</td>
                    <td>{v.change_source || '—'}</td>
                    <td>{v.word_count ?? '—'}</td>
                    <td>{v.workflow_state_at_save || '—'}</td>
                    <td>{(v.change_note || '').slice(0, 50)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Select
              label="Restore content to version"
              options={versions.map((v) => ({
                value: String(v.version_id),
                label: `${v.version_number || v.version_id} — ${v.change_source || ''} (${v.created_at ? formatDateTime(v.created_at) : '?'})`,
              }))}
              value={restoreVerId}
              onChange={(e) => setRestoreVerId(e.target.value)}
            />
            <Button
              variant="primary"
              size="sm"
              disabled={!restoreVerId}
              onClick={async () => {
                await dispatch(restoreBlockVersionThunk({ blockId: block.id, versionId: Number(restoreVerId) }));
                await refreshBlocks();
              }}
            >
              ⏪ Restore Selected Version
            </Button>
          </>
        )}
        <input
          className={styles.inlineInput}
          placeholder="Snapshot note (optional)"
          value={snapNote}
          onChange={(e) => setSnapNote(e.target.value)}
        />
        <Button
          variant="secondary"
          size="sm"
          onClick={async () => {
            await dispatch(createSnapshotThunk({ blockId: block.id, label: snapNote }));
            await loadVersions();
          }}
        >
          💾 Save Snapshot
        </Button>
        </div>
      </details>

      <hr className={styles.blockDivider} />
    </article>
  );
}
