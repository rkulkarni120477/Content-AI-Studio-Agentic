import { useEffect, useMemo, useState, Fragment } from 'react';
import { useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { cn, formatRelative } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ErrorState from '@components/common/ErrorState/ErrorState';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import Modal from '@components/common/Modal/Modal';
import Select from '@components/common/Select/Select';
import { renderMarkdownPreview } from '@utils/markdownPreview';
import {
  fetchFeedbackThunk, analyzeFeedbackThunk, recommendFeedbackThunk,
  deleteFeedbackItemThunk, bulkDeleteFeedbackThunk,
} from '../../feedbackThunks';
import {
  selectFeedbackItems, selectFeedbackLoading,
  selectFeedbackProcessing, selectFeedbackRecommending, selectFeedbackError,
} from '../../feedbackSlice';
import { fetchModelsThunk } from '@features/dashboard/dashboardThunks';
import { selectModels, selectModelChoice } from '@features/dashboard/dashboardSlice';
import styles from './FeedbackPage.module.scss';

const SENTIMENTS = {
  suggestion: { label: 'Suggestion', cls: 'sugg',    icon: '💡' },
  concern:    { label: 'Concern',    cls: 'concern', icon: '⚠️' },
  praise:     { label: 'Praise',     cls: 'praise',  icon: '👍' },
  neutral:    { label: 'Neutral',    cls: 'neutral', icon: '•'  },
};
const PRIORITIES = {
  high:   { label: 'High',   cls: 'high' },
  medium: { label: 'Medium', cls: 'med' },
  low:    { label: 'Low',    cls: 'low' },
};

const ACCEPTED = '.pptx,.docx,.pdf,.xlsx,.txt';

export default function FeedbackPage() {
  const { courseId } = useParams();
  const dispatch = useAppDispatch();

  const items       = useAppSelector(selectFeedbackItems);
  const isLoading   = useAppSelector(selectFeedbackLoading);
  const isProcessing = useAppSelector(selectFeedbackProcessing);
  const recommendingIds = useAppSelector(selectFeedbackRecommending);
  const error       = useAppSelector(selectFeedbackError);
  const models      = useAppSelector(selectModels);
  const projectModel = useAppSelector(selectModelChoice);

  const recommending = useMemo(() => new Set(recommendingIds), [recommendingIds]);

  const [search, setSearch]           = useState('');
  const [themeFilter, setThemeFilter] = useState('');
  const [sentFilter, setSentFilter]   = useState('');
  const [recFilter, setRecFilter]     = useState('');   // '' | 'has' | 'none'
  const [selected, setSelected]       = useState(() => new Set());
  const [expanded, setExpanded]       = useState(() => new Set()); // rows showing their recommendation
  const [regen, setRegen]             = useState(null); // { id, guidance, model } — regenerate dialog
  const [pendingDelete, setPendingDelete] = useState(null); // { mode:'one'|'many', id? }

  useEffect(() => {
    if (courseId) dispatch(fetchFeedbackThunk(courseId));
  }, [courseId, dispatch]);

  useEffect(() => { dispatch(fetchModelsThunk()); }, [dispatch]);

  // Drop selections that no longer exist after a refresh/delete.
  useEffect(() => {
    setSelected((prev) => {
      const ids = new Set(items.map((i) => i.id));
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [items]);

  const themes = useMemo(
    () => [...new Set(items.map((i) => i.theme).filter(Boolean))].sort(),
    [items],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((i) => {
      const matchesQ = !q
        || (i.feedback_text || '').toLowerCase().includes(q)
        || (i.theme || '').toLowerCase().includes(q)
        || (i.source_location || '').toLowerCase().includes(q);
      const matchesTheme = !themeFilter || i.theme === themeFilter;
      const matchesSent  = !sentFilter || i.sentiment === sentFilter;
      const hasRec = i.recommendation_status === 'ready' && !!i.recommendation;
      const matchesRec = !recFilter
        || (recFilter === 'has' && hasRec)
        || (recFilter === 'none' && !hasRec);
      return matchesQ && matchesTheme && matchesSent && matchesRec;
    });
  }, [items, search, themeFilter, sentFilter, recFilter]);

  const stats = useMemo(() => ({
    total:      items.length,
    suggestion: items.filter((i) => i.sentiment === 'suggestion').length,
    concern:    items.filter((i) => i.sentiment === 'concern').length,
    recommended: items.filter((i) => i.recommendation_status === 'ready' && i.recommendation).length,
  }), [items]);

  function handleUpload(files) {
    const file = files?.[0];
    if (file && courseId) {
      dispatch(analyzeFeedbackThunk({ file, courseId }));
    }
  }

  function handleRecommend(ids, opts = {}) {
    const list = [...new Set(ids)].filter((id) => !recommending.has(id));
    if (!list.length) return;
    dispatch(recommendFeedbackThunk({
      itemIds: list,
      guidance: opts.guidance,
      modelChoice: opts.modelChoice,
    })).unwrap().catch(() => {
      // A client-side timeout can abort the request while the server keeps
      // running and commits. Refetch so the table reflects server truth
      // instead of silently stranding recommendations that actually landed.
      if (courseId) dispatch(fetchFeedbackThunk(courseId));
    });
    // Open the panels so the user sees results (and the in-flight state) land.
    setExpanded((prev) => new Set([...prev, ...list]));
  }

  function openRegen(item) {
    setRegen({
      id: item.id,
      guidance: '',
      model: item.recommendation_model || projectModel || '',
    });
  }

  function submitRegen() {
    if (!regen) return;
    handleRecommend([regen.id], { guidance: regen.guidance, modelChoice: regen.model });
    setRegen(null);
  }

  // Model options for the regenerate dialog, grouped by provider (dynamic — a
  // future provider is never silently dropped) with the current value always
  // present so the controlled <Select> shows what will actually be sent.
  function modelGroups() {
    const list = models?.items || [];
    const labelOf = (m) => m.display_name || m.name;
    const current = regen?.model;
    if (!list.length) {
      const opts = [...new Set([current, projectModel].filter(Boolean))]
        .map((m) => ({ value: m, label: m }));
      return [{ label: 'Model', options: opts }];
    }
    const PROVIDER_LABELS = { openai: 'OpenAI', bedrock: 'AWS Bedrock' };
    const order = [];
    const byProvider = new Map();
    list.forEach((m) => {
      const key = (m.provider || 'other').toLowerCase();
      if (!byProvider.has(key)) { byProvider.set(key, []); order.push(key); }
      byProvider.get(key).push({ value: labelOf(m), label: labelOf(m) });
    });
    const groups = order.map((key) => ({
      label: PROVIDER_LABELS[key] || key.charAt(0).toUpperCase() + key.slice(1),
      options: byProvider.get(key),
    }));
    if (current && !list.some((m) => labelOf(m) === current)) {
      groups.unshift({ label: 'Current', options: [{ value: current, label: current }] });
    }
    return groups;
  }

  function handleCopy(text) {
    if (!text || !navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(
      () => toast.success('Recommendation copied'),
      () => toast.error('Could not copy to clipboard'),
    );
  }

  function toggleExpand(id) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleOne(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => {
      const visibleIds = filtered.map((i) => i.id);
      const allSelected = visibleIds.length > 0 && visibleIds.every((id) => prev.has(id));
      if (allSelected) {
        const next = new Set(prev);
        visibleIds.forEach((id) => next.delete(id));
        return next;
      }
      return new Set([...prev, ...visibleIds]);
    });
  }

  const allVisibleSelected = filtered.length > 0 && filtered.every((i) => selected.has(i.id));

  function confirmDelete() {
    if (!pendingDelete) return;
    if (pendingDelete.mode === 'one') {
      dispatch(deleteFeedbackItemThunk(pendingDelete.id));
    } else {
      dispatch(bulkDeleteFeedbackThunk([...selected]));
      setSelected(new Set());
    }
    setPendingDelete(null);
  }

  const breadcrumbs = [
    { label: 'Workspace' },
    { label: 'Feedback' },
  ];

  return (
    <PageContainer title="Reviewer Feedback" breadcrumbs={breadcrumbs}>
      <div className={styles.page}>
        <p className={styles.intro}>
          Upload a review document — PPTX, DOCX, PDF, XLSX or TXT — and let AI extract each
          piece of reviewer feedback into a structured, actionable table linked to this course.
        </p>

        {/* Upload zone */}
        <div className={styles.uploadCard}>
          <FileUpload
            accept={ACCEPTED}
            multiple={false}
            disabled={isProcessing}
            onChange={handleUpload}
            label="Drop a feedback document here or click to browse"
            hint="PPTX · DOCX · PDF · XLSX · TXT"
          />
          {isProcessing && (
            <div className={styles.processing}>
              <Loader size="sm" />
              <span>Analysing document with AI — extracting feedback…</span>
            </div>
          )}
        </div>

        {error && (
          <ErrorState
            message={error}
            onRetry={() => dispatch(fetchFeedbackThunk(courseId))}
          />
        )}

        {/* Stats */}
        {items.length > 0 && (
          <div className={styles.stats}>
            <Stat n={stats.total} label="Feedback items" tone="accent" />
            <Stat n={stats.suggestion} label="Suggestions" tone="warn" />
            <Stat n={stats.concern} label="Concerns" tone="danger" />
            <Stat n={stats.recommended} label="Recommendations" tone="ok" />
          </div>
        )}

        {/* Table card */}
        {isLoading ? (
          <div className={styles.center}><Loader size="lg" /></div>
        ) : items.length === 0 ? (
          <EmptyState
            icon="💬"
            title="No feedback yet"
            message="Upload a reviewer document above to extract feedback for this course."
          />
        ) : (
          <div className={styles.card}>
            <div className={styles.toolbar}>
              <div className={styles.search}>
                <span className={styles.search__icon} aria-hidden="true">🔍</span>
                <input
                  type="text"
                  placeholder="Search feedback…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  aria-label="Search feedback"
                />
              </div>
              <select
                className={styles.filter}
                value={themeFilter}
                onChange={(e) => setThemeFilter(e.target.value)}
                aria-label="Filter by theme"
              >
                <option value="">All themes</option>
                {themes.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <select
                className={styles.filter}
                value={sentFilter}
                onChange={(e) => setSentFilter(e.target.value)}
                aria-label="Filter by sentiment"
              >
                <option value="">All sentiment</option>
                <option value="suggestion">Suggestion</option>
                <option value="concern">Concern</option>
                <option value="praise">Praise</option>
                <option value="neutral">Neutral</option>
              </select>
              <select
                className={styles.filter}
                value={recFilter}
                onChange={(e) => setRecFilter(e.target.value)}
                aria-label="Filter by recommendation"
              >
                <option value="">All items</option>
                <option value="has">✦ Has recommendation</option>
                <option value="none">No recommendation</option>
              </select>
            </div>

            {recommendingIds.length > 0 && (
              <div className={styles.recBanner}>
                <Loader size="xs" />
                Generating {recommendingIds.length} recommendation
                {recommendingIds.length === 1 ? '' : 's'}…
              </div>
            )}

            {selected.size > 0 && (
              <div className={styles.selbar}>
                <span>{selected.size} selected</span>
                <div className={styles.selbar__actions}>
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={[...selected].every((id) => recommending.has(id))}
                    onClick={() => handleRecommend([...selected])}
                  >
                    ✦ Recommend for selected
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => setPendingDelete({ mode: 'many' })}
                  >
                    Delete selected
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                    Clear
                  </Button>
                </div>
              </div>
            )}

            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th className={styles.colCheck}>
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={toggleAll}
                        aria-label="Select all"
                      />
                    </th>
                    <th className={styles.colId}>#</th>
                    <th>Feedback</th>
                    <th>Theme</th>
                    <th>Sentiment</th>
                    <th>Priority</th>
                    <th className={styles.colActions}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((item, idx) => {
                    const s = SENTIMENTS[item.sentiment] || SENTIMENTS.neutral;
                    const p = PRIORITIES[item.priority] || PRIORITIES.medium;
                    const isBusy = recommending.has(item.id);
                    const hasRec = item.recommendation_status === 'ready' && !!item.recommendation;
                    const isError = item.recommendation_status === 'error';
                    const isOpen = expanded.has(item.id);
                    return (
                      <Fragment key={item.id}>
                        <tr className={cn(hasRec && styles.hasRecRow)}>
                          <td className={styles.colCheck}>
                            <input
                              type="checkbox"
                              checked={selected.has(item.id)}
                              onChange={() => toggleOne(item.id)}
                              aria-label={`Select item ${idx + 1}`}
                            />
                          </td>
                          <td className={styles.colId}>{idx + 1}</td>
                          <td className={styles.fbText}>
                            <div>
                              {item.feedback_text}
                              {hasRec && (
                                <span className={styles.recDot} title="Has an AI recommendation" />
                              )}
                            </div>
                            {(item.source_location || item.document_name) && (
                              <div className={styles.fbSrc}>
                                📄 {item.source_location || item.document_name}
                              </div>
                            )}
                          </td>
                          <td>
                            {item.theme && <span className={styles.chip}>{item.theme}</span>}
                          </td>
                          <td>
                            <span className={cn(styles.pill, styles[`pill--${s.cls}`])}>
                              <span aria-hidden="true">{s.icon}</span> {s.label}
                            </span>
                          </td>
                          <td>
                            <span className={cn(styles.prio, styles[`prio--${p.cls}`])}>
                              <span className={styles.prio__bar}><i /></span>
                              {p.label}
                            </span>
                          </td>
                          <td className={styles.colActions}>
                            <div className={styles.rowActions}>
                              {isBusy ? (
                                <span className={styles.recBusy}>
                                  <Loader size="xs" /> Generating…
                                </span>
                              ) : hasRec ? (
                                <button
                                  type="button"
                                  className={styles.linkBtn}
                                  onClick={() => toggleExpand(item.id)}
                                >
                                  {isOpen ? 'Hide' : 'View'} recommendation
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  className={styles.recBtn}
                                  onClick={() => handleRecommend([item.id])}
                                >
                                  ✦ {isError ? 'Retry' : 'Recommend'}
                                </button>
                              )}
                              <button
                                type="button"
                                className={styles.iconBtn}
                                title="Delete"
                                aria-label="Delete feedback item"
                                onClick={() => setPendingDelete({ mode: 'one', id: item.id })}
                              >
                                🗑
                              </button>
                            </div>
                          </td>
                        </tr>

                        {(isBusy || (hasRec && isOpen) || isError) && (
                          <tr className={styles.recRow}>
                            <td colSpan={7}>
                              {isBusy ? (
                                <div className={styles.recPanel}>
                                  <div className={styles.recLoading}>
                                    <Loader size="sm" />
                                    <div>
                                      <div className={styles.recLoading__t}>
                                        Analysing feedback against this course’s content…
                                      </div>
                                      <div className={styles.recLoading__s}>
                                        Loading course blocks → selecting relevant content → generating recommendation
                                      </div>
                                    </div>
                                  </div>
                                </div>
                              ) : isError ? (
                                <div className={styles.recPanel}>
                                  <div className={styles.recError}>
                                    ⚠️ Couldn’t generate a recommendation for this item.
                                    <button
                                      type="button"
                                      className={styles.linkBtn}
                                      onClick={() => handleRecommend([item.id])}
                                    >
                                      Try again
                                    </button>
                                  </div>
                                </div>
                              ) : (
                                <div className={styles.recPanel}>
                                  <div className={styles.recInner}>
                                    <div className={styles.recHead}>
                                      <span className={styles.recTitle}>
                                        <span className={styles.recTitle__ai}>✦</span> AI Recommendation
                                      </span>
                                      {item.recommendation_model && (
                                        <span className={styles.recModel}>{item.recommendation_model}</span>
                                      )}
                                    </div>
                                    <div
                                      className={cn(styles.recBody, 'markdown-content')}
                                      dangerouslySetInnerHTML={{
                                        __html: renderMarkdownPreview(item.recommendation || ''),
                                      }}
                                    />
                                    {item.recommendation_refs?.length > 0 && (
                                      <div className={styles.recRefs}>
                                        <div className={styles.recRefs__l}>Referenced course content</div>
                                        <div className={styles.recRefs__chips}>
                                          {item.recommendation_refs.map((r) => (
                                            <span key={r} className={styles.refChip}>📄 {r}</span>
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                    <div className={styles.recFoot}>
                                      <button
                                        type="button"
                                        className={styles.linkBtn}
                                        onClick={() => handleCopy(item.recommendation)}
                                      >
                                        ⧉ Copy
                                      </button>
                                      <button
                                        type="button"
                                        className={styles.linkBtn}
                                        onClick={() => openRegen(item)}
                                      >
                                        ↻ Regenerate
                                      </button>
                                      <button
                                        type="button"
                                        className={styles.linkBtn}
                                        onClick={() => toggleExpand(item.id)}
                                      >
                                        Hide
                                      </button>
                                      {item.recommended_at && (
                                        <span className={styles.recTime}>
                                          Generated {formatRelative(item.recommended_at)}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              {filtered.length === 0 && (
                <div className={styles.emptyFilter}>No feedback matches your filters.</div>
              )}
            </div>

            <div className={styles.foot}>
              <span>Showing {filtered.length} of {items.length} items</span>
              <span>Extracted by AI · review before relying on it</span>
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!pendingDelete}
        onClose={() => setPendingDelete(null)}
        onConfirm={confirmDelete}
        title={pendingDelete?.mode === 'many' ? `Delete ${selected.size} feedback items?` : 'Delete this feedback item?'}
        message={
          pendingDelete?.mode === 'many'
            ? 'The selected items will be removed from this course. This cannot be undone.'
            : 'This item will be removed from this course. This cannot be undone.'
        }
        confirmLabel="Delete"
        variant="danger"
      />

      <Modal
        open={!!regen}
        onClose={() => setRegen(null)}
        title="Regenerate recommendation"
        size="md"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setRegen(null)}>Cancel</Button>
            <Button variant="primary" onClick={submitRegen}>↻ Regenerate</Button>
          </>
        )}
      >
        {regen && (
          <div className={styles.regen}>
            {items.find((i) => i.id === regen.id)?.feedback_text && (
              <p className={styles.regen__ctx}>
                {items.find((i) => i.id === regen.id).feedback_text}
              </p>
            )}
            <label className={styles.regen__label} htmlFor="regen-guidance">
              Guidance <span>(optional)</span>
            </label>
            <textarea
              id="regen-guidance"
              className={styles.regen__input}
              rows={3}
              placeholder="e.g. focus on the assessment items; give exact replacement wording; keep it shorter"
              value={regen.guidance}
              onChange={(e) => setRegen((r) => ({ ...r, guidance: e.target.value }))}
            />
            <Select
              label="Model"
              groups={modelGroups()}
              value={regen.model}
              onChange={(e) => setRegen((r) => ({ ...r, model: e.target.value }))}
              hint="Defaults to this course's model. Overriding applies to this run only."
            />
          </div>
        )}
      </Modal>
    </PageContainer>
  );
}

function Stat({ n, label, tone }) {
  return (
    <div className={cn(styles.stat, styles[`stat--${tone}`])}>
      <div className={styles.stat__n}>{n}</div>
      <div className={styles.stat__l}>{label}</div>
    </div>
  );
}
