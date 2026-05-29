import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchWorkflowBlocksThunk, approveBlockThunk, requestChangesThunk,
  publishBlockThunk, archiveBlockThunk, bulkApproveThunk, fetchPendingReviewsThunk,
} from '@features/workflow/workflowThunks';
import {
  selectBlocksByState, selectWorkflowFilters, selectPendingCount,
  selectWorkflowLoading, setFilters,
} from '@features/workflow/workflowSlice';
import { WORKFLOW_STATES, WORKFLOW_STATE_LABELS } from '@utils/constants';
import { formatRelative, getSlaStatus } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import PageContainer from '@components/layout/PageContainer/PageContainer';
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
  const dispatch      = useAppDispatch();
  const blocksByState = useAppSelector(selectBlocksByState);
  const filters       = useAppSelector(selectWorkflowFilters);
  const pendingCount  = useAppSelector(selectPendingCount);
  const isLoading     = useAppSelector(selectWorkflowLoading);
  const { canApprove, user } = useAuth();

  const [selectedIds, setSelectedIds]         = useState([]);
  const [confirmAction, setConfirmAction]     = useState(null); // { type, blockId }

  useEffect(() => {
    dispatch(fetchWorkflowBlocksThunk(filters));
    dispatch(fetchPendingReviewsThunk());
  }, [dispatch, filters]);

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
      case 'approve':   await dispatch(approveBlockThunk({ blockId })); break;
      case 'changes':   await dispatch(requestChangesThunk({ blockId })); break;
      case 'publish':   await dispatch(publishBlockThunk(blockId)); break;
      case 'archive':   await dispatch(archiveBlockThunk(blockId)); break;
      case 'bulk':      await dispatch(bulkApproveThunk(selectedIds)); setSelectedIds([]); break;
    }
  }

  const ACTION_LABELS = { approve: 'Approve', changes: 'Request Changes', publish: 'Publish', archive: 'Archive', bulk: 'Bulk Approve' };

  return (
    <PageContainer
      title="Workflow"
      breadcrumbs={[{ label: 'Workflow' }]}
      headerActions={
        canApprove && selectedIds.length > 0 && (
          <Button variant="success" size="sm" onClick={() => handleAction('bulk')}>
            ✅ Approve {selectedIds.length} selected
          </Button>
        )
      }
    >
      {/* Pending badge */}
      {pendingCount > 0 && (
        <div className={styles.pendingBanner}>
          👀 You have <strong>{pendingCount}</strong> block(s) pending your review.
        </div>
      )}

      {/* Filters */}
      <div className={styles.filters}>
        <SearchBar
          value={filters.search || ''}
          onChange={(v) => handleFilter('search', v)}
          placeholder="Search block label…"
          className={styles.filters__search}
        />
        <Select
          options={[
            { value: '', label: 'All Statuses' },
            ...KANBAN_COLUMNS.map((c) => ({ value: c.key, label: c.label })),
          ]}
          value={filters.status || ''}
          onChange={(e) => handleFilter('status', e.target.value)}
          wrapperClassName={styles.filters__select}
        />
      </div>

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
                            <Button variant="ghost" size="xs" onClick={() => handleAction('approve', block.id)}>Submit</Button>
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

      <ConfirmDialog
        open={Boolean(confirmAction)}
        onClose={() => setConfirmAction(null)}
        onConfirm={confirmWorkflowAction}
        title={`${ACTION_LABELS[confirmAction?.type] || 'Confirm'}`}
        message={`Are you sure you want to ${ACTION_LABELS[confirmAction?.type]?.toLowerCase()} this block?`}
        confirmLabel={ACTION_LABELS[confirmAction?.type] || 'Confirm'}
        variant={['changes', 'archive'].includes(confirmAction?.type) ? 'danger' : 'success'}
      />
    </PageContainer>
  );
}
