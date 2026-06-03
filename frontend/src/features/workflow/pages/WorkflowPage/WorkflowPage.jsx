import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchWorkflowBlocksThunk, submitBlockThunk, approveBlockThunk, requestChangesThunk,
  publishBlockThunk, archiveBlockThunk, bulkApproveThunk, fetchPendingReviewsThunk,
} from '@features/workflow/workflowThunks';
import {
  selectSelectedProject, selectSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import {
  selectBlocksByState, selectWorkflowFilters, selectPendingCount,
  selectWorkflowLoading, setFilters,
} from '@features/workflow/workflowSlice';
import { WORKFLOW_STATES, WORKFLOW_STATE_LABELS } from '@utils/constants';
import { formatRelative, getSlaStatus } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import Select from '@components/common/Select/Select';
import SearchBar from '@components/common/SearchBar/SearchBar';
import Button from '@components/common/Button/Button';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import Loader from '@components/common/Loader/Loader';
import styles from './WorkflowPage.module.scss';

const KANBAN_COLUMNS = [
  { key: WORKFLOW_STATES.DRAFT,             label: 'Draft',              icon: '📝' },
  { key: WORKFLOW_STATES.IN_REVIEW,         label: 'In Review',          icon: '👀' },
  { key: WORKFLOW_STATES.CHANGES_REQUESTED, label: 'Changes Requested',  icon: '✏️' },
  { key: WORKFLOW_STATES.APPROVED,          label: 'Approved',           icon: '✅' },
  { key: WORKFLOW_STATES.PUBLISHED,         label: 'Published',          icon: '🌐' },
  { key: WORKFLOW_STATES.ARCHIVED,          label: 'Archived',           icon: '📦' },
];

export default function WorkflowPage() {
  const { courseId } = useParams();
  const dispatch      = useAppDispatch();
  const blocksByState = useAppSelector(selectBlocksByState);
  const filters       = useAppSelector(selectWorkflowFilters);
  const pendingCount  = useAppSelector(selectPendingCount);
  const isLoading     = useAppSelector(selectWorkflowLoading);
  const selProject = useAppSelector(selectSelectedProject);
  const selCourse = useAppSelector(selectSelectedCourse);
  const { canApprove, user, isAdmin } = useAuth();

  const [selectedIds, setSelectedIds]         = useState([]);
  const [confirmAction, setConfirmAction]     = useState(null);
  const [approvalBlockId, setApprovalBlockId] = useState(null);
  const [reviewerName, setReviewerName] = useState('');

  const allBlocks = KANBAN_COLUMNS.flatMap((c) => blocksByState[c.key] || []);
  const approvalBlock = allBlocks.find((b) => b.id === approvalBlockId);

  function buildApiFilters() {
    return {
      ...filters,
      project_id: filters.projectId ?? selProject?.id,
      course_id: filters.courseId ?? (courseId ? Number(courseId) : undefined),
      state: filters.status || undefined,
      search: filters.search || undefined,
    };
  }

  useEffect(() => {
    dispatch(fetchWorkflowBlocksThunk(buildApiFilters()));
    dispatch(fetchPendingReviewsThunk());
  }, [dispatch, filters, selProject?.id, courseId]);

  function handleFilter(key, value) {
    dispatch(setFilters({ [key]: value }));
  }

  function handleAction(type, blockId) {
    setConfirmAction({ type, blockId });
  }

  async function confirmWorkflowAction() {
    if (!confirmAction) return;
    const { type, blockId } = confirmAction;
    setConfirmAction(null);

    switch (type) {
      case 'submit':
        await dispatch(submitBlockThunk({ blockId, reviewer: reviewerName || user?.username }));
        break;
      case 'approve':   await dispatch(approveBlockThunk({ blockId })); break;
      case 'changes':   await dispatch(requestChangesThunk({ blockId })); break;
      case 'publish':   await dispatch(publishBlockThunk(blockId)); break;
      case 'archive':   await dispatch(archiveBlockThunk(blockId)); break;
      case 'bulk':      await dispatch(bulkApproveThunk(selectedIds)); setSelectedIds([]); break;
    }
    dispatch(fetchWorkflowBlocksThunk(buildApiFilters()));
  }

  const ACTION_LABELS = {
    submit: 'Submit for Review', approve: 'Approve', changes: 'Request Changes',
    publish: 'Publish', archive: 'Archive', bulk: 'Bulk Approve',
  };

  return (
    <PageContainer
      title=""
      breadcrumbs={[{ label: 'Workflow' }]}
      noPadding
      headerActions={
        canApprove && selectedIds.length > 0 && (
          <Button variant="success" size="sm" onClick={() => handleAction('bulk')}>
            ✅ Approve {selectedIds.length} selected
          </Button>
        )
      }
    >
      <div className={styles.page}>
        <SectionBadge
          icon="🚦"
          title="Content Lifecycle"
          subtitle="Manage the approval pipeline — submit, review, approve, publish, and archive content blocks."
        />

      {!isAdmin && selProject && (
        <p className={styles.scopeCaption}>
          Your workflow — project <strong>{selProject.name}</strong>
          {selCourse ? <> · course <strong>{selCourse.name}</strong></> : null}
        </p>
      )}
      {isAdmin && <p className={styles.scopeCaption}>Admin view — all projects and users.</p>}

      {/* Pending badge */}
      {pendingCount > 0 && (
        <div className={styles.pendingBanner}>
          👀 You have <strong>{pendingCount}</strong> block(s) pending your review.
        </div>
      )}

      <h2 className={styles.sectionTitle}>🔍 Filters</h2>
      <div className={styles.filters}>
        <Select
          options={[
            { value: '', label: 'All Statuses' },
            ...KANBAN_COLUMNS.map((c) => ({ value: c.key, label: c.label })),
          ]}
          value={filters.status || ''}
          onChange={(e) => handleFilter('status', e.target.value)}
          wrapperClassName={styles.filters__select}
          label="Status"
        />
        <SearchBar
          value={filters.search || ''}
          onChange={(v) => handleFilter('search', v)}
          placeholder="Search block label…"
          className={styles.filters__search}
        />
      </div>

      <h2 className={styles.sectionTitle}>📋 Status Overview</h2>

      {/* Kanban Board */}
      {isLoading ? (
        <div className={styles.loading}><Loader size="xl" /></div>
      ) : (
        <div className={styles.kanban}>
          {KANBAN_COLUMNS.map((col) => {
            const colBlocks = blocksByState[col.key] || [];
            return (
              <div key={col.key} className={styles.column}>
                <div className={styles.column__header}>
                  <span>{col.icon} {col.label}</span>
                  <span className={styles.column__count}>{colBlocks.length}</span>
                </div>
                <div className={styles.column__cards}>
                  {colBlocks.map((block) => {
                    const sla = col.key === WORKFLOW_STATES.IN_REVIEW
                      ? getSlaStatus(block.submitted_at)
                      : null;
                    return (
                      <div
                        key={block.id}
                        className={`${styles.blockCard} ${sla === 'overdue' ? styles['blockCard--overdue'] : ''}`}
                      >
                        {canApprove && col.key === WORKFLOW_STATES.IN_REVIEW && (
                          <input
                            type="checkbox"
                            aria-label={`Select ${block.block_label}`}
                            checked={selectedIds.includes(block.id)}
                            onChange={(e) => {
                              setSelectedIds((prev) =>
                                e.target.checked ? [...prev, block.id] : prev.filter((id) => id !== block.id),
                              );
                            }}
                            className={styles.blockCard__check}
                          />
                        )}
                        <div className={styles.blockCard__header}>
                          <span className={styles.blockCard__label}>{block.block_label || `Block ${block.id}`}</span>
                          {sla && (
                            <span className={`${styles.slaBadge} ${styles[`slaBadge--${sla}`]}`}>
                              {sla === 'overdue' ? '⚠️ Overdue' : sla === 'warning' ? '⏰ Due soon' : ''}
                            </span>
                          )}
                        </div>
                        <div className={styles.blockCard__meta}>
                          <span>{block.block_type || 'lesson'}</span>
                          <span>·</span>
                          <span>{formatRelative(block.created_at)}</span>
                        </div>
                        <div className={styles.blockCard__actions}>
                          {col.key === WORKFLOW_STATES.DRAFT && (
                            <Button variant="ghost" size="xs" onClick={() => handleAction('submit', block.id)}>Submit</Button>
                          )}
                          {col.key === WORKFLOW_STATES.IN_REVIEW && canApprove && (
                            <>
                              <Button variant="ghost" size="xs" onClick={() => handleAction('approve', block.id)}>Approve</Button>
                              <Button variant="ghost" size="xs" onClick={() => handleAction('changes', block.id)}>Changes</Button>
                            </>
                          )}
                          {col.key === WORKFLOW_STATES.APPROVED && canApprove && (
                            <Button variant="ghost" size="xs" onClick={() => handleAction('publish', block.id)}>Publish</Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  {colBlocks.length === 0 && (
                    <p className={styles.emptyCol}>No blocks</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <h2 className={styles.sectionTitle}>⚙️ Approval Center</h2>
      {allBlocks.length > 0 ? (
        <div className={styles.approvalCenter}>
          <Select
            label="Select Block"
            options={allBlocks.map((b) => ({
              value: String(b.id),
              label: `#${b.id} — ${(b.block_label || '').slice(0, 40)} [${WORKFLOW_STATE_LABELS[b.workflow_state] || b.workflow_state}]`,
            }))}
            value={approvalBlockId != null ? String(approvalBlockId) : ''}
            onChange={(e) => setApprovalBlockId(Number(e.target.value))}
          />
          {approvalBlock && (
            <div className={styles.approvalDetail}>
              <p><strong>Block #{approvalBlock.id}</strong> — {approvalBlock.block_label}</p>
              <p>Status: {WORKFLOW_STATE_LABELS[approvalBlock.workflow_state] || approvalBlock.workflow_state}</p>
              {approvalBlock.workflow_state === WORKFLOW_STATES.DRAFT && (
                <div className={styles.approvalActions}>
                  <input
                    type="text"
                    placeholder="Reviewer username"
                    value={reviewerName}
                    onChange={(e) => setReviewerName(e.target.value)}
                    className={styles.reviewerInput}
                  />
                  <Button variant="primary" size="sm" onClick={() => handleAction('submit', approvalBlock.id)}>
                    📤 Submit for Review
                  </Button>
                </div>
              )}
              {approvalBlock.workflow_state === WORKFLOW_STATES.IN_REVIEW && canApprove && (
                <div className={styles.approvalActions}>
                  <Button variant="success" size="sm" onClick={() => handleAction('approve', approvalBlock.id)}>✅ Approve</Button>
                  <Button variant="danger-ghost" size="sm" onClick={() => handleAction('changes', approvalBlock.id)}>🔁 Request Changes</Button>
                </div>
              )}
              {approvalBlock.workflow_state === WORKFLOW_STATES.APPROVED && canApprove && (
                <Button variant="secondary" size="sm" onClick={() => handleAction('publish', approvalBlock.id)}>🌐 Publish</Button>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className={styles.emptyHint}>No content blocks match your filters. Generate content from the Generate tab to begin.</p>
      )}

      <ConfirmDialog
        open={Boolean(confirmAction)}
        onClose={() => setConfirmAction(null)}
        onConfirm={confirmWorkflowAction}
        title={`${ACTION_LABELS[confirmAction?.type] || 'Confirm'}`}
        message={`Are you sure you want to ${ACTION_LABELS[confirmAction?.type]?.toLowerCase()} this block?`}
        confirmLabel={ACTION_LABELS[confirmAction?.type] || 'Confirm'}
        variant={['changes', 'archive'].includes(confirmAction?.type) ? 'danger' : 'success'}
      />
      </div>
    </PageContainer>
  );
}
