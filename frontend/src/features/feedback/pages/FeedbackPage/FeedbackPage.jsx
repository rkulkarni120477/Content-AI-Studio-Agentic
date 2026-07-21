import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { cn } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import FileUpload from '@components/common/FileUpload/FileUpload';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import ErrorState from '@components/common/ErrorState/ErrorState';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import {
  fetchFeedbackThunk, analyzeFeedbackThunk,
  deleteFeedbackItemThunk, bulkDeleteFeedbackThunk,
} from '../../feedbackThunks';
import {
  selectFeedbackItems, selectFeedbackLoading,
  selectFeedbackProcessing, selectFeedbackError,
} from '../../feedbackSlice';
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
  const error       = useAppSelector(selectFeedbackError);

  const [search, setSearch]           = useState('');
  const [themeFilter, setThemeFilter] = useState('');
  const [sentFilter, setSentFilter]   = useState('');
  const [selected, setSelected]       = useState(() => new Set());
  const [pendingDelete, setPendingDelete] = useState(null); // { mode:'one'|'many', id? }

  useEffect(() => {
    if (courseId) dispatch(fetchFeedbackThunk(courseId));
  }, [courseId, dispatch]);

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
      return matchesQ && matchesTheme && matchesSent;
    });
  }, [items, search, themeFilter, sentFilter]);

  const stats = useMemo(() => ({
    total:      items.length,
    suggestion: items.filter((i) => i.sentiment === 'suggestion').length,
    concern:    items.filter((i) => i.sentiment === 'concern').length,
    praise:     items.filter((i) => i.sentiment === 'praise').length,
  }), [items]);

  function handleUpload(files) {
    const file = files?.[0];
    if (file && courseId) {
      dispatch(analyzeFeedbackThunk({ file, courseId }));
    }
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
            <Stat n={stats.praise} label="Praise" tone="ok" />
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
            </div>

            {selected.size > 0 && (
              <div className={styles.selbar}>
                <span>{selected.size} selected</span>
                <div className={styles.selbar__actions}>
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
                    return (
                      <tr key={item.id}>
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
                          <div>{item.feedback_text}</div>
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
                          <button
                            type="button"
                            className={styles.iconBtn}
                            title="Delete"
                            aria-label="Delete feedback item"
                            onClick={() => setPendingDelete({ mode: 'one', id: item.id })}
                          >
                            🗑
                          </button>
                        </td>
                      </tr>
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
