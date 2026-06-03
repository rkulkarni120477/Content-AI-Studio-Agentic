import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchBlocksThunk, updateBlockThunk, fetchBlockVersionsThunk,
  restoreBlockVersionThunk, submitBlockThunk, exportCourseThunk,
  triggerPlagiarismThunk, validateCourseThunk,
} from '@features/editor/editorThunks';
import {
  selectBlocks, selectSelectedBlock, selectBlockVersions,
  selectValidation, selectEditorLoading, selectEditorExporting,
  selectEditorValidating, selectEditorError, selectBlock, clearSelectedBlock,
} from '@features/editor/editorSlice';
import { editBlockSchema, reviewSchema } from '@utils/validation';
import { WORKFLOW_STATES, WORKFLOW_STATE_LABELS, EXPORT_FORMATS } from '@utils/constants';
import { getWorkflowStateColor, formatDate } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import Table from '@components/common/Table/Table';
import Button from '@components/common/Button/Button';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import ErrorState from '@components/common/ErrorState/ErrorState';
import styles from './EditorPage.module.scss';

const STATE_COLOR_MAP = {
  [WORKFLOW_STATES.DRAFT]:             styles.badge__draft,
  [WORKFLOW_STATES.IN_REVIEW]:         styles.badge__review,
  [WORKFLOW_STATES.CHANGES_REQUESTED]: styles.badge__changes,
  [WORKFLOW_STATES.APPROVED]:          styles.badge__approved,
  [WORKFLOW_STATES.PUBLISHED]:         styles.badge__published,
  [WORKFLOW_STATES.ARCHIVED]:          styles.badge__archived,
};

export default function EditorPage() {
  const { courseId } = useParams();
  const dispatch     = useAppDispatch();
  const { canApprove, isAdmin } = useAuth();

  const blocks        = useAppSelector(selectBlocks);
  const selectedBlock = useAppSelector(selectSelectedBlock);
  const blockVersions = useAppSelector(selectBlockVersions);
  const validation    = useAppSelector(selectValidation);
  const isLoading     = useAppSelector(selectEditorLoading);
  const isExporting   = useAppSelector(selectEditorExporting);
  const isValidating  = useAppSelector(selectEditorValidating);
  const error         = useAppSelector(selectEditorError);

  const [editMode, setEditMode] = useState(false);
  const [showVersions, setShowVersions] = useState(false);

  const editForm = useForm({ resolver: zodResolver(editBlockSchema) });

  useEffect(() => {
    dispatch(fetchBlocksThunk(courseId));
  }, [courseId, dispatch]);

  function handleSelectBlock(block) {
    dispatch(selectBlock(block));
    editForm.reset({ content: block.content, edit_reason: '' });
    if (block.id) dispatch(fetchBlockVersionsThunk(block.id));
  }

  async function onSaveEdit(data) {
    if (!selectedBlock) return;
    await dispatch(updateBlockThunk({ blockId: selectedBlock.id, data }));
    setEditMode(false);
  }

  async function onWorkflowAction(action, data) {
    if (!selectedBlock) return;
    await dispatch(submitBlockThunk({ blockId: selectedBlock.id, action, data }));
  }

  async function onExport(format) {
    dispatch(exportCourseThunk({
      courseId,
      format,
      filename: `course-${courseId}.${format}`,
    }));
  }

  const COLUMNS = [
    {
      key:      'block_label',
      header:   'Label',
      sortable: true,
      render: (v, row) => <button className={styles.blockLink} onClick={() => handleSelectBlock(row)}>{v || '—'}</button>,
    },
    {
      key:    'block_type',
      header: 'Type',
      render: (v) => <span className={styles.badge__type}>{v || '—'}</span>,
    },
    {
      key:    'workflow_state',
      header: 'Status',
      render: (v) => (
        <span className={`${styles.badge} ${STATE_COLOR_MAP[v] || ''}`}>
          {WORKFLOW_STATE_LABELS[v] || v || 'Draft'}
        </span>
      ),
    },
    {
      key:    'created_at',
      header: 'Created',
      render: (v) => formatDate(v),
    },
    {
      key:    'rating',
      header: 'Rating',
      align:  'center',
      render: (v) => v ? `${v}/5` : '—',
    },
  ];

  // Approved blocks count for course completion check
  const approvedCount  = blocks.filter((b) => [WORKFLOW_STATES.APPROVED, WORKFLOW_STATES.PUBLISHED].includes(b.workflow_state)).length;
  const allApproved    = blocks.length > 0 && approvedCount === blocks.length;

  const validationIssues = validation?.blocks?.flatMap((b) => b.errors || [])
    || validation?.issues
    || [];

  return (
    <PageContainer
      title=""
      breadcrumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Editor' }]}
      noPadding
      headerActions={
        <div className={styles.headerActions}>
          <Button variant="ghost" size="sm" loading={isValidating} onClick={() => dispatch(validateCourseThunk(courseId))}>
            ✓ Validate
          </Button>
          <Button variant="ghost" size="sm" loading={isExporting} onClick={() => onExport('docx')}>
            ↓ DOCX
          </Button>
          <Button variant="secondary" size="sm" loading={isExporting} onClick={() => onExport('pdf')}>
            ↓ PDF
          </Button>
        </div>
      }
    >
      <div className={styles.page}>
        <SectionBadge
          icon="✏️"
          title="Content Editor"
          subtitle="Review, edit, validate, and export generated lesson blocks. Submit blocks to the workflow when ready for review."
        />

      {/* Course completion banner */}
      {allApproved && (
        <div className={styles.completionBanner}>
          <div className={styles.completionBanner__icon}>🎉</div>
          <div>
            <strong>Course Generation Complete!</strong>
            <p>All blocks are approved. Validate content below before downloading the full course package.</p>
          </div>
        </div>
      )}

      {/* Validation results */}
      {validation && (
        <div className={`${styles.validation} ${validation.passed ? styles['validation--pass'] : styles['validation--fail']}`}>
          {validation.passed ? '✅ Validation passed' : `❌ ${validationIssues.length || 'Some'} issue(s) found`}
          {!validation.passed && validationIssues.length > 0 && (
            <ul className={styles.validation__issues}>
              {validationIssues.map((issue, i) => <li key={i}>{typeof issue === 'string' ? issue : JSON.stringify(issue)}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className={styles.layout}>
        {/* Blocks Table */}
        <section className={styles.tablePanel}>
          <Table
            columns={COLUMNS}
            rows={blocks}
            rowKey="id"
            isLoading={isLoading}
            pagination
            pageSize={20}
            emptyTitle="No blocks generated yet"
            emptyMessage="Generate content from the Generate tab."
            onRowClick={handleSelectBlock}
          />
          {error && <ErrorState message={error} onRetry={() => dispatch(fetchBlocksThunk(courseId))} />}
        </section>

        {/* Block Detail Panel */}
        {selectedBlock && (
          <section className={styles.detailPanel}>
            <div className={styles.detailPanel__header}>
              <h3 className={styles.detailPanel__title}>{selectedBlock.block_label}</h3>
              <div className={styles.detailPanel__actions}>
                <Button variant="ghost" size="sm" onClick={() => { dispatch(clearSelectedBlock()); setEditMode(false); }}>✕</Button>
              </div>
            </div>

            <div className={styles.detailPanel__meta}>
              <span className={`${styles.badge} ${STATE_COLOR_MAP[selectedBlock.workflow_state] || ''}`}>
                {WORKFLOW_STATE_LABELS[selectedBlock.workflow_state] || 'Draft'}
              </span>
              <span className={styles.detailPanel__type}>{selectedBlock.block_type}</span>
            </div>

            {/* Workflow Actions */}
            <div className={styles.workflowActions}>
              {selectedBlock.workflow_state === WORKFLOW_STATES.DRAFT && (
                <Button variant="primary" size="sm" onClick={() => onWorkflowAction('submit')}>
                  Submit for Review
                </Button>
              )}
              {selectedBlock.workflow_state === WORKFLOW_STATES.IN_REVIEW && canApprove && (
                <>
                  <Button variant="success" size="sm" onClick={() => onWorkflowAction('approve')}>Approve</Button>
                  <Button variant="danger-ghost" size="sm" onClick={() => onWorkflowAction('request_changes')}>Request Changes</Button>
                </>
              )}
              {selectedBlock.workflow_state === WORKFLOW_STATES.APPROVED && canApprove && (
                <Button variant="secondary" size="sm" onClick={() => onWorkflowAction('publish')}>Publish</Button>
              )}
              {[WORKFLOW_STATES.DRAFT, WORKFLOW_STATES.CHANGES_REQUESTED].includes(selectedBlock.workflow_state) && (
                <Button variant="ghost" size="sm" onClick={() => setEditMode(!editMode)}>
                  {editMode ? 'Cancel Edit' : '✏️ Edit'}
                </Button>
              )}
              <Button variant="ghost" size="sm" onClick={() => { setShowVersions(true); dispatch(fetchBlockVersionsThunk(selectedBlock.id)); }}>
                History
              </Button>
              <Button variant="ghost" size="sm" onClick={() => dispatch(triggerPlagiarismThunk(selectedBlock.id))}>
                🔍 Plagiarism
              </Button>
            </div>

            {/* Edit Form */}
            {editMode ? (
              <form onSubmit={editForm.handleSubmit(onSaveEdit)} className={styles.editForm}>
                <textarea
                  className={styles.editForm__textarea}
                  rows={14}
                  {...editForm.register('content')}
                />
                {editForm.formState.errors.content && (
                  <p className={styles.editForm__error}>{editForm.formState.errors.content.message}</p>
                )}
                <div className={styles.form__label}>Edit Reason</div>
                <input className={styles.editForm__input} type="text" placeholder="Optional" {...editForm.register('edit_reason')} />
                <Button type="submit" variant="primary" size="sm">Save</Button>
              </form>
            ) : (
              <div className={`${styles.contentView} markdown-content`}>
                <pre className={styles.contentView__text}>{selectedBlock.content}</pre>
              </div>
            )}
          </section>
        )}
      </div>

      {/* Version History Modal */}
      <Modal
        open={showVersions}
        onClose={() => setShowVersions(false)}
        title="Version History"
        size="md"
      >
        {blockVersions.length === 0 ? (
          <p className={styles.emptyText}>No saved versions yet.</p>
        ) : (
          <ul className={styles.versionList}>
            {blockVersions.map((v) => (
              <li key={v.id} className={styles.versionItem}>
                <div>
                  <span className={styles.versionItem__ver}>v{v.version}</span>
                  <span className={styles.versionItem__date}>{formatDate(v.created_at)}</span>
                  {v.edit_reason && <span className={styles.versionItem__reason}>{v.edit_reason}</span>}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    dispatch(restoreBlockVersionThunk({ blockId: selectedBlock.id, version: v.version }));
                    setShowVersions(false);
                  }}
                >
                  Restore
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Modal>
      </div>
    </PageContainer>
  );
}
