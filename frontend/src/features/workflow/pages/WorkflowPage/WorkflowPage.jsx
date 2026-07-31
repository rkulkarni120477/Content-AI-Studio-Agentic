import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchWorkflowBlocksThunk, submitBlockThunk, approveBlockThunk, requestChangesThunk,
  rejectBlockThunk, publishBlockThunk, archiveBlockThunk, resetDraftBlockThunk,
  bulkApproveThunk, fetchPendingReviewsThunk,
} from '@features/workflow/workflowThunks';
import {
  selectSelectedProject, selectSelectedCourse, selectProjects,
} from '@features/dashboard/dashboardSlice';
import { fetchProjectsThunk } from '@features/dashboard/dashboardThunks';
import {
  selectBlocksByState, selectWorkflowBlocks, selectWorkflowFilters,
  selectPendingCount, selectWorkflowLoading, setFilters,
} from '@features/workflow/workflowSlice';
import {
  WORKFLOW_STATES, WORKFLOW_STATE_LABELS, WORKFLOW_KANBAN_COLORS,
} from '@utils/constants';
import { truncate } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import toast from 'react-hot-toast';
import { api } from '@services/apiClient';
import { PROJECTS, USERS } from '@services/endpoints';
import { workflowService } from '@features/workflow/services/workflowService';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import Select from '@components/common/Select/Select';
import SearchBar from '@components/common/SearchBar/SearchBar';
import Button from '@components/common/Button/Button';
import MultiSelect from '@components/common/MultiSelect/MultiSelect';
import Loader from '@components/common/Loader/Loader';
import WorkflowStatusBadge from '@features/editor/components/WorkflowStatusBadge/WorkflowStatusBadge';
import { useLabels } from '@hooks/useLabels';
import styles from './WorkflowPage.module.scss';

const KANBAN_COLUMNS = [
  WORKFLOW_STATES.DRAFT,
  WORKFLOW_STATES.IN_REVIEW,
  WORKFLOW_STATES.CHANGES_REQUESTED,
  WORKFLOW_STATES.APPROVED,
  WORKFLOW_STATES.PUBLISHED,
  WORKFLOW_STATES.ARCHIVED,
];

const SUBMITTABLE = new Set([
  WORKFLOW_STATES.DRAFT,
  WORKFLOW_STATES.REJECTED,
  WORKFLOW_STATES.CHANGES_REQUESTED,
]);

const RESETTABLE = new Set([
  WORKFLOW_STATES.APPROVED,
  WORKFLOW_STATES.REJECTED,
  WORKFLOW_STATES.CHANGES_REQUESTED,
]);

/** Allowed drag-drop targets keyed by current workflow state. */
const KANBAN_DROP_TARGETS = {
  [WORKFLOW_STATES.DRAFT]: [WORKFLOW_STATES.IN_REVIEW],
  [WORKFLOW_STATES.IN_REVIEW]: [
    WORKFLOW_STATES.APPROVED,
    WORKFLOW_STATES.CHANGES_REQUESTED,
  ],
  [WORKFLOW_STATES.CHANGES_REQUESTED]: [
    WORKFLOW_STATES.IN_REVIEW,
    WORKFLOW_STATES.DRAFT,
  ],
  [WORKFLOW_STATES.APPROVED]: [
    WORKFLOW_STATES.PUBLISHED,
    WORKFLOW_STATES.ARCHIVED,
    WORKFLOW_STATES.DRAFT,
  ],
  [WORKFLOW_STATES.PUBLISHED]: [WORKFLOW_STATES.ARCHIVED],
  [WORKFLOW_STATES.ARCHIVED]: [],
};

function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

function formatDt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function filterBlocks(blocks, filters) {
  let list = [...blocks];
  if (filters.status) {
    list = list.filter((b) => b.workflow_state === filters.status);
  }
  if (filters.reviewer) {
    list = list.filter((b) => b.assigned_reviewer === filters.reviewer);
  }
  if (filters.search?.trim()) {
    const q = filters.search.trim().toLowerCase();
    list = list.filter((b) => (b.block_label || '').toLowerCase().includes(q));
  }
  return list;
}

export default function WorkflowPage() {
  const L = useLabels();
  const { courseId: routeCourseId } = useParams();
  const workspaceCourseId = routeCourseId ? Number(routeCourseId) : null;
  const dispatch = useAppDispatch();
  const blocksByState = useAppSelector(selectBlocksByState);
  const allRawBlocks = useAppSelector(selectWorkflowBlocks);
  const filters = useAppSelector(selectWorkflowFilters);
  const pendingCount = useAppSelector(selectPendingCount);
  const isLoading = useAppSelector(selectWorkflowLoading);
  const selProject = useAppSelector(selectSelectedProject);
  const selCourse = useAppSelector(selectSelectedCourse);
  const projectsData = useAppSelector(selectProjects);
  const { user, isAdmin, canApprove, hasPermission } = useAuth();

  const [approvalBlockId, setApprovalBlockId] = useState(null);
  const [reviewerName, setReviewerName] = useState('');
  const [reviewers, setReviewers] = useState([]);
  const [projectCourses, setProjectCourses] = useState([]);
  const [rejectMode, setRejectMode] = useState(false);
  const [reviewComment, setReviewComment] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const [events, setEvents] = useState([]);
  const [bulkSelected, setBulkSelected] = useState([]);
  const [bulkResult, setBulkResult] = useState(null);
  const [expandedProjects, setExpandedProjects] = useState({});
  const [adminBreakdown, setAdminBreakdown] = useState([]);
  const [breakdownLoading, setBreakdownLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [dragBlockId, setDragBlockId] = useState(null);
  const [dragFromState, setDragFromState] = useState(null);
  const [dropTargetState, setDropTargetState] = useState(null);
  const approvalCenterRef = useRef(null);
  const suppressKanbanClickRef = useRef(false);

  const showAdminBreakdown = isAdmin || hasPermission('analytics.view_all');

  const projects = useMemo(() => normalizeList(projectsData), [projectsData]);

  const buildApiFilters = useCallback(() => {
    const f = {};
    if (filters.projectId != null) {
      f.project_id = filters.projectId;
    } else if (!isAdmin && selProject?.id) {
      f.project_id = selProject.id;
    } else if (selCourse?.project_id) {
      f.project_id = selCourse.project_id;
    }
    if (filters.courseId != null) {
      f.course_id = filters.courseId;
    } else if (workspaceCourseId) {
      f.course_id = workspaceCourseId;
    } else if (selCourse?.id) {
      f.course_id = selCourse.id;
    }
    if (filters.search?.trim()) f.search = filters.search.trim();
    if (filters.reviewer) f.reviewer = filters.reviewer;
    return f;
  }, [filters, isAdmin, selProject?.id, selCourse?.id, selCourse?.project_id, workspaceCourseId]);

  const loadAdminBreakdown = useCallback(() => {
    if (!showAdminBreakdown) return;
    workflowService.getAdminBreakdown()
      .then((data) => setAdminBreakdown(normalizeList(data)))
      .catch(() => setAdminBreakdown([]));
  }, [showAdminBreakdown]);

  const refresh = useCallback(() => {
    dispatch(fetchWorkflowBlocksThunk(buildApiFilters()));
    if (canApprove || isAdmin) dispatch(fetchPendingReviewsThunk());
    loadAdminBreakdown();
  }, [dispatch, buildApiFilters, canApprove, isAdmin, loadAdminBreakdown]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (isAdmin) dispatch(fetchProjectsThunk());
  }, [dispatch, isAdmin]);

  useEffect(() => {
    if (!showAdminBreakdown) return;
    setBreakdownLoading(true);
    workflowService.getAdminBreakdown()
      .then((data) => {
        const items = normalizeList(data);
        setAdminBreakdown(items);
        const open = {};
        for (const p of items) open[p.project_id] = true;
        setExpandedProjects(open);
      })
      .catch(() => setAdminBreakdown([]))
      .finally(() => setBreakdownLoading(false));
  }, [showAdminBreakdown]); // initial load only

  useEffect(() => {
    if (!canApprove && !isAdmin) return;
    api.get(USERS.REVIEWERS)
      .then((res) => {
        const list = normalizeList(res);
        setReviewers(list.map((u) => u.username || u).filter(Boolean));
      })
      .catch(() => setReviewers([]));
  }, [canApprove, isAdmin]);

  useEffect(() => {
    const projId = filters.projectId ?? (!isAdmin ? selProject?.id : null) ?? selCourse?.project_id;
    if (!projId) {
      setProjectCourses([]);
      return;
    }
    api.get(PROJECTS.COURSES(projId))
      .then((res) => setProjectCourses(normalizeList(res)))
      .catch(() => setProjectCourses([]));
  }, [filters.projectId, isAdmin, selProject?.id, selCourse?.project_id]);

  const filteredBlocks = useMemo(
    () => filterBlocks(normalizeList(allRawBlocks), filters),
    [allRawBlocks, filters],
  );

  useEffect(() => {
    if (!filteredBlocks.length) {
      setApprovalBlockId(null);
      return;
    }
    if (!filteredBlocks.some((b) => b.id === approvalBlockId)) {
      setApprovalBlockId(filteredBlocks[0].id);
    }
  }, [filteredBlocks, approvalBlockId]);

  const approvalBlock = filteredBlocks.find((b) => b.id === approvalBlockId);

  useEffect(() => {
    setRejectMode(false);
    setReviewComment('');
    setRejectReason('');
    if (!approvalBlockId) {
      setEvents([]);
      return;
    }
    workflowService.getEvents(approvalBlockId)
      .then((res) => setEvents(normalizeList(res)))
      .catch(() => setEvents([]));
  }, [approvalBlockId]);

  useEffect(() => {
    if (reviewers.length && !reviewerName) setReviewerName(reviewers[0]);
  }, [reviewers, reviewerName]);

  const inReviewBlocks = blocksByState[WORKFLOW_STATES.IN_REVIEW] || [];

  useEffect(() => {
    const opts = inReviewBlocks.map((b) => String(b.id));
    setBulkSelected(opts);
  }, [inReviewBlocks.map((b) => b.id).join(',')]);

  function handleFilter(key, value) {
    const patch = { [key]: value };
    if (key === 'projectId') patch.courseId = null;
    dispatch(setFilters(patch));
  }

  async function runAction(fn) {
    setActionLoading(true);
    try {
      await fn();
      refresh();
    } finally {
      setActionLoading(false);
    }
  }

  const selectKanbanBlock = useCallback((blockId) => {
    if (suppressKanbanClickRef.current) {
      suppressKanbanClickRef.current = false;
      return;
    }
    setApprovalBlockId(blockId);
    // Defer scroll so Approval Center reflects the new selection first.
    requestAnimationFrame(() => {
      approvalCenterRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }, []);

  const clearDrag = useCallback(() => {
    if (dragBlockId != null) suppressKanbanClickRef.current = true;
    setDragBlockId(null);
    setDragFromState(null);
    setDropTargetState(null);
  }, [dragBlockId]);

  const canDropOnColumn = useCallback((fromState, toState) => {
    if (!fromState || !toState || fromState === toState) return false;
    return (KANBAN_DROP_TARGETS[fromState] || []).includes(toState);
  }, []);

  async function applyKanbanTransition(blockId, fromState, toState) {
    if (fromState === toState) return;
    if (!canDropOnColumn(fromState, toState)) {
      toast.error(
        `Cannot move from ${WORKFLOW_STATE_LABELS[fromState] || fromState} to ${WORKFLOW_STATE_LABELS[toState] || toState}`,
      );
      return;
    }

    const needsReviewer = toState === WORKFLOW_STATES.APPROVED
      || toState === WORKFLOW_STATES.CHANGES_REQUESTED;
    if (needsReviewer && !canApprove && !isAdmin) {
      toast.error('Only reviewers can move blocks into this status.');
      return;
    }

    try {
      if (toState === WORKFLOW_STATES.IN_REVIEW && SUBMITTABLE.has(fromState)) {
        await dispatch(submitBlockThunk({
          blockId,
          reviewer: reviewerName || undefined,
        })).unwrap();
      } else if (
        fromState === WORKFLOW_STATES.IN_REVIEW
        && toState === WORKFLOW_STATES.APPROVED
      ) {
        await dispatch(approveBlockThunk({ blockId, comment: '' })).unwrap();
      } else if (
        fromState === WORKFLOW_STATES.IN_REVIEW
        && toState === WORKFLOW_STATES.CHANGES_REQUESTED
      ) {
        const reason = window.prompt('Reason for requesting changes:');
        if (!reason?.trim()) {
          toast.error('A reason is required to request changes.');
          return;
        }
        await dispatch(requestChangesThunk({ blockId, reason: reason.trim() })).unwrap();
      } else if (
        toState === WORKFLOW_STATES.PUBLISHED
        && fromState === WORKFLOW_STATES.APPROVED
      ) {
        await dispatch(publishBlockThunk(blockId)).unwrap();
      } else if (
        toState === WORKFLOW_STATES.ARCHIVED
        && (fromState === WORKFLOW_STATES.APPROVED || fromState === WORKFLOW_STATES.PUBLISHED)
      ) {
        await dispatch(archiveBlockThunk(blockId)).unwrap();
      } else if (
        toState === WORKFLOW_STATES.DRAFT
        && RESETTABLE.has(fromState)
      ) {
        await dispatch(resetDraftBlockThunk(blockId)).unwrap();
      } else {
        toast.error('This status change is not supported via drag and drop.');
        return;
      }
      setApprovalBlockId(blockId);
      refresh();
    } catch (err) {
      toast.error(typeof err === 'string' ? err : (err?.message || 'Status change failed'));
    }
  }

  const scopeProjId = filters.projectId ?? (!isAdmin ? selProject?.id : null) ?? selCourse?.project_id;
  const showCourseFilter = Boolean(scopeProjId) && projectCourses.length > 0;

  const blockSelectOptions = filteredBlocks.map((b) => ({
    value: String(b.id),
    label: `#${b.id} — ${(b.block_label || '').slice(0, 40)} [${WORKFLOW_STATE_LABELS[b.workflow_state] || b.workflow_state}]`,
  }));

  const bulkOptions = inReviewBlocks.map((b) => ({
    value: String(b.id),
    label: `#${b.id} — ${(b.block_label || '').slice(0, 40)}`,
  }));

  const stateKey = approvalBlock?.workflow_state?.toLowerCase();

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Workflow' }]} noPadding>
      <div className={styles.page}>
        <SectionBadge
          icon="🚦"
          title="Content Lifecycle"
          subtitle="Manage the approval pipeline — submit, review, approve, publish, and archive content blocks."
        />

        {(canApprove || isAdmin) && pendingCount > 0 && (
          <div className={styles.pendingBanner}>
            📬 <strong>You have {pendingCount} block(s) pending your review.</strong>{' '}
            See the <em>Approval Center</em> below.
          </div>
        )}

        <h2 className={styles.sectionTitle}>🔍 Filters</h2>
        <div className={styles.filters}>
          <Select
            label="Status"
            options={[
              { value: '', label: 'All statuses' },
              ...KANBAN_COLUMNS.map((s) => ({
                value: s,
                label: WORKFLOW_STATE_LABELS[s],
              })),
            ]}
            value={filters.status || ''}
            onChange={(e) => handleFilter('status', e.target.value)}
          />
          <Select
            label="Reviewer"
            options={[
              { value: '', label: 'All reviewers' },
              ...reviewers.map((r) => ({ value: r, label: r })),
            ]}
            value={filters.reviewer || ''}
            onChange={(e) => handleFilter('reviewer', e.target.value)}
          />
          <div>
            <label className={styles.metaLine} style={{ display: 'block', marginBottom: 4 }}>
              🔍 Search label
            </label>
            <SearchBar
              value={filters.search || ''}
              onChange={(v) => handleFilter('search', v)}
              placeholder="e.g. Lesson 1"
            />
          </div>
          {isAdmin ? (
            <Select
              label="Project"
              options={[
                { value: '', label: 'All projects' },
                ...projects.map((p) => ({ value: String(p.id), label: p.name })),
              ]}
              value={filters.projectId != null ? String(filters.projectId) : ''}
              onChange={(e) => handleFilter('projectId', e.target.value ? Number(e.target.value) : null)}
            />
          ) : (
            <p className={styles.filters__projectCaption}>
              Project: <strong>{selProject?.name || '—'}</strong>
            </p>
          )}
        </div>
        <div className={styles.filters__row2}>
          {showCourseFilter ? (
            <Select
              label={L.title}
              options={[
                { value: '', label: `All ${L.titlesLower}` },
                ...projectCourses.map((c) => ({ value: String(c.id), label: c.name })),
              ]}
              value={filters.courseId != null ? String(filters.courseId) : ''}
              onChange={(e) => handleFilter('courseId', e.target.value ? Number(e.target.value) : null)}
            />
          ) : scopeProjId ? (
            <p className={styles.filters__noCourses}>No titles in this project.</p>
          ) : null}
        </div>

        <h2 className={styles.sectionTitle}>📋 Status Overview</h2>
        {!isAdmin && selProject && (
          <p className={styles.scopeCaption}>
            Your workflow — project <strong>{selProject.name}</strong>
            {selCourse ? <> · title <strong>{selCourse.name}</strong></> : null}
          </p>
        )}
        {isAdmin && (
          <p className={styles.scopeCaption}>Admin view — all projects and users.</p>
        )}

        {isLoading ? (
          <div className={styles.loading}><Loader size="xl" /></div>
        ) : (
          <div className={styles.kanban}>
            {KANBAN_COLUMNS.map((state) => {
              const colBlocks = blocksByState[state] || [];
              const color = WORKFLOW_KANBAN_COLORS[state];
              const label = WORKFLOW_STATE_LABELS[state];
              const isDropTarget = dropTargetState === state
                && canDropOnColumn(dragFromState, state);
              return (
                <div
                  key={state}
                  className={[
                    styles.column,
                    isDropTarget ? styles['column--dropTarget'] : '',
                  ].filter(Boolean).join(' ')}
                >
                  <div
                    className={styles.column__header}
                    style={{ borderBottomColor: color }}
                  >
                    {label}
                    <span className={styles.column__count} style={{ color }}>
                      ({colBlocks.length})
                    </span>
                  </div>
                  <div
                    className={styles.column__cards}
                    onDragOver={(e) => {
                      if (!canDropOnColumn(dragFromState, state)) return;
                      e.preventDefault();
                      e.dataTransfer.dropEffect = 'move';
                      if (dropTargetState !== state) setDropTargetState(state);
                    }}
                    onDragLeave={(e) => {
                      if (!e.currentTarget.contains(e.relatedTarget)) {
                        setDropTargetState((cur) => (cur === state ? null : cur));
                      }
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      const raw = e.dataTransfer.getData('application/json')
                        || e.dataTransfer.getData('text/plain');
                      let payload;
                      try {
                        payload = JSON.parse(raw);
                      } catch {
                        payload = null;
                      }
                      const blockId = payload?.blockId ?? dragBlockId;
                      const fromState = payload?.fromState ?? dragFromState;
                      clearDrag();
                      if (blockId == null || !fromState) return;
                      void applyKanbanTransition(Number(blockId), fromState, state);
                    }}
                  >
                    {colBlocks.length === 0 ? (
                      <p className={styles.emptyDash}>—</p>
                    ) : (
                      colBlocks.map((block) => {
                        const isSelected = approvalBlockId === block.id;
                        const isDragging = dragBlockId === block.id;
                        return (
                          <button
                            type="button"
                            key={block.id}
                            draggable
                            className={[
                              styles.kanbanCard,
                              isSelected ? styles['kanbanCard--selected'] : '',
                              isDragging ? styles['kanbanCard--dragging'] : '',
                            ].filter(Boolean).join(' ')}
                            style={{ borderLeftColor: `${color}22` }}
                            title="Click to open in Approval Center · Drag to change status"
                            onClick={() => selectKanbanBlock(block.id)}
                            onDragStart={(e) => {
                              e.dataTransfer.effectAllowed = 'move';
                              e.dataTransfer.setData(
                                'application/json',
                                JSON.stringify({ blockId: block.id, fromState: state }),
                              );
                              e.dataTransfer.setData('text/plain', String(block.id));
                              setDragBlockId(block.id);
                              setDragFromState(state);
                            }}
                            onDragEnd={clearDrag}
                          >
                            <p className={styles.kanbanCard__label}>
                              {truncate(block.block_label || `Block ${block.id}`, 25)}
                            </p>
                            <p className={styles.kanbanCard__meta}>
                              #{block.id}
                              {block.assigned_reviewer && (
                                <span className={styles.kanbanCard__reviewer}>
                                  {' '}→ {block.assigned_reviewer}
                                </span>
                              )}
                              {block.sla?.label && (
                                <span className={styles.kanbanCard__sla}> {block.sla.label}</span>
                              )}
                            </p>
                          </button>
                        );
                      })
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <hr className={styles.divider} />

        <h2 ref={approvalCenterRef} className={styles.sectionTitle}>⚙️ Approval Center</h2>

        {filteredBlocks.length === 0 ? (
          <p className={styles.emptyHint}>
            No content blocks match your filters. Generate content from the <strong>Generate</strong> tab to begin.
          </p>
        ) : (
          <>
            <Select
              label="Select Block"
              options={blockSelectOptions}
              value={approvalBlockId != null ? String(approvalBlockId) : ''}
              onChange={(e) => setApprovalBlockId(Number(e.target.value))}
            />
            {approvalBlock && (
              <div className={styles.approvalGrid}>
                <div className={styles.approvalMeta}>
                  <p>
                    <strong>Block #{approvalBlock.id}</strong> — {approvalBlock.block_label}
                  </p>
                  <WorkflowStatusBadge state={approvalBlock.workflow_state} />
                  {approvalBlock.submitted_by && (
                    <p className={styles.metaLine}>📤 Submitted by: <strong>{approvalBlock.submitted_by}</strong></p>
                  )}
                  {approvalBlock.assigned_reviewer && (
                    <p className={styles.metaLine}>👤 Reviewer: <strong>{approvalBlock.assigned_reviewer}</strong></p>
                  )}
                  {approvalBlock.approved_by && (
                    <p className={styles.metaLine}>✅ Approved by: <strong>{approvalBlock.approved_by}</strong></p>
                  )}
                  {approvalBlock.reviewed_by && [WORKFLOW_STATES.CHANGES_REQUESTED, WORKFLOW_STATES.REJECTED].includes(stateKey) && (
                    <p className={styles.metaLine}>🔍 Reviewed by: <strong>{approvalBlock.reviewed_by}</strong></p>
                  )}
                  {approvalBlock.review_comments && [WORKFLOW_STATES.CHANGES_REQUESTED, WORKFLOW_STATES.REJECTED, WORKFLOW_STATES.APPROVED].includes(stateKey) && (
                    <p className={styles.metaLine}>💬 Comments: <em>{approvalBlock.review_comments.slice(0, 120)}</em></p>
                  )}
                  {approvalBlock.archived_by && (
                    <p className={styles.metaLine}>🗄️ Archived by: <strong>{approvalBlock.archived_by}</strong></p>
                  )}
                  {approvalBlock.sla?.label && (
                    <p className={styles.metaLine}>{approvalBlock.sla.label}</p>
                  )}
                  {approvalBlock.review_requested_at && (
                    <p className={styles.metaLine}>🕐 Submitted: {formatDt(approvalBlock.review_requested_at)}</p>
                  )}
                  {events.length > 0 && (
                    <details className={styles.history}>
                      <summary>📜 Transition History</summary>
                      {events.map((ev, i) => (
                        <p key={i} className={styles.history__item}>
                          {formatDt(ev.created_at)} <strong>{ev.actor}</strong> — {ev.from_state} → {ev.to_state} ({ev.action})
                          {ev.comment ? <> · <em>{ev.comment.slice(0, 60)}</em></> : null}
                        </p>
                      ))}
                    </details>
                  )}
                </div>

                <div className={styles.actionsCard}>
                  <h3>Available Actions</h3>

                  {SUBMITTABLE.has(stateKey) && hasPermission('workflow.submit') && (
                    <div className={styles.actionForm}>
                      <Select
                        label="Assign Reviewer"
                        options={(reviewers.length ? reviewers : ['(no reviewers available)']).map((r) => ({
                          value: r,
                          label: r,
                        }))}
                        value={reviewerName}
                        onChange={(e) => setReviewerName(e.target.value)}
                        disabled={!reviewers.length}
                      />
                      <Button
                        variant="primary"
                        disabled={actionLoading || !reviewers.length}
                        onClick={() => runAction(() =>
                          dispatch(submitBlockThunk({
                            blockId: approvalBlock.id,
                            reviewer: reviewerName,
                          })).unwrap(),
                        )}
                      >
                        📤 Submit for Review
                      </Button>
                    </div>
                  )}

                  {stateKey === WORKFLOW_STATES.IN_REVIEW && hasPermission('workflow.approve') && !rejectMode && (
                    <div className={styles.actionForm}>
                      <label className={styles.metaLine}>Review Comment</label>
                      <textarea
                        className={styles.textarea}
                        placeholder="Optional notes for the author…"
                        value={reviewComment}
                        onChange={(e) => setReviewComment(e.target.value)}
                      />
                      <div className={styles.reviewBtns}>
                        <Button
                          variant="success"
                          disabled={actionLoading}
                          onClick={() => runAction(() =>
                            dispatch(approveBlockThunk({
                              blockId: approvalBlock.id,
                              comment: reviewComment,
                            })).unwrap(),
                          )}
                        >
                          ✅ Approve
                        </Button>
                        <Button
                          variant="secondary"
                          disabled={actionLoading}
                          onClick={() => {
                            if (!reviewComment.trim()) {
                              toast.error('Please describe what changes are needed.');
                              return;
                            }
                            runAction(() =>
                              dispatch(requestChangesThunk({
                                blockId: approvalBlock.id,
                                reason: reviewComment,
                              })).unwrap(),
                            );
                          }}
                        >
                          🔁 Request Changes
                        </Button>
                        <Button
                          variant="danger-ghost"
                          disabled={actionLoading}
                          onClick={() => setRejectMode(true)}
                        >
                          ❌ Reject
                        </Button>
                      </div>
                    </div>
                  )}

                  {stateKey === WORKFLOW_STATES.IN_REVIEW && rejectMode && (
                    <div className={styles.actionForm}>
                      <label className={styles.metaLine}>Rejection Reason (required)</label>
                      <textarea
                        className={styles.textarea}
                        placeholder="Explain why this content is rejected…"
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                      />
                      <div className={styles.reviewBtns}>
                        <Button
                          variant="danger"
                          disabled={actionLoading}
                          onClick={() => {
                            if (!rejectReason.trim()) return;
                            runAction(() =>
                              dispatch(rejectBlockThunk({
                                blockId: approvalBlock.id,
                                reason: rejectReason,
                              })).unwrap(),
                            ).then(() => setRejectMode(false));
                          }}
                        >
                          Confirm Rejection
                        </Button>
                        <Button variant="ghost" onClick={() => setRejectMode(false)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  )}

                  {stateKey === WORKFLOW_STATES.APPROVED && hasPermission('workflow.publish') && (
                    <Button
                      variant="primary"
                      disabled={actionLoading}
                      onClick={() => runAction(() =>
                        dispatch(publishBlockThunk(approvalBlock.id)).unwrap(),
                      )}
                    >
                      🚀 Publish Block
                    </Button>
                  )}

                  {RESETTABLE.has(stateKey) && hasPermission('workflow.reset_draft') && (
                    <Button
                      variant="secondary"
                      disabled={actionLoading}
                      onClick={() => runAction(() =>
                        dispatch(resetDraftBlockThunk(approvalBlock.id)).unwrap(),
                      )}
                    >
                      ↩️ Reset to Draft
                    </Button>
                  )}

                  {[WORKFLOW_STATES.APPROVED, WORKFLOW_STATES.PUBLISHED].includes(stateKey)
                    && hasPermission('workflow.archive') && (
                    <Button
                      variant="ghost"
                      disabled={actionLoading}
                      onClick={() => runAction(() =>
                        dispatch(archiveBlockThunk(approvalBlock.id)).unwrap(),
                      )}
                    >
                      🗄️ Archive Block
                    </Button>
                  )}

                  {stateKey === WORKFLOW_STATES.PUBLISHED && (
                    <div className={styles.alertSuccess}>
                      ✅ This block is Published — no further transitions available.
                    </div>
                  )}
                  {stateKey === WORKFLOW_STATES.ARCHIVED && (
                    <div className={styles.alertInfo}>🗄️ This block is Archived.</div>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {hasPermission('workflow.bulk_approve') && (
          <>
            <hr className={styles.divider} />
            <section className={styles.bulkSection}>
              <h2 className={styles.sectionTitle}>⚡ Bulk Approve</h2>
              {inReviewBlocks.length === 0 ? (
                <p className={styles.emptyHint}>No blocks are currently In Review.</p>
              ) : (
                <>
                  <MultiSelect
                    label={`Select blocks to approve (${inReviewBlocks.length} in review)`}
                    options={bulkOptions}
                    value={bulkSelected}
                    onChange={setBulkSelected}
                  />
                  {bulkSelected.length > 0 && (
                    <Button
                      variant="primary"
                      disabled={actionLoading}
                      onClick={() => runAction(async () => {
                        const result = await dispatch(
                          bulkApproveThunk(bulkSelected.map(Number)),
                        ).unwrap();
                        setBulkResult(result);
                      })}
                    >
                      ✅ Bulk Approve {bulkSelected.length} block(s)
                    </Button>
                  )}
                  {bulkResult && (
                    <p className={styles.bulkResult}>
                      Approved: {bulkResult.approved?.length ?? 0} | Skipped: {bulkResult.skipped?.length ?? 0} | Errors: {bulkResult.errors?.length ?? 0}
                    </p>
                  )}
                </>
              )}
            </section>
          </>
        )}

        {showAdminBreakdown && (
          <>
            <hr className={styles.divider} />
            <section className={styles.adminSection}>
              <h2 className={styles.sectionTitle}>🗂️ Admin — All Projects · User Breakdown</h2>
              {breakdownLoading ? (
                <div className={styles.breakdownLoading}><Loader size="md" /></div>
              ) : adminBreakdown.length === 0 ? (
                <p className={styles.emptyHint}>No project block data to display.</p>
              ) : (
                adminBreakdown.map((proj) => {
                  const open = expandedProjects[proj.project_id] ?? true;
                  return (
                    <div key={proj.project_id} className={styles.projectExpander}>
                      <button
                        type="button"
                        className={styles.projectExpander__header}
                        onClick={() => setExpandedProjects((p) => ({
                          ...p,
                          [proj.project_id]: !open,
                        }))}
                      >
                        <span className={styles.projectExpander__chevron} aria-hidden="true">
                          {open ? '▼' : '▶'}
                        </span>
                        <span className={styles.projectExpander__folder} aria-hidden="true">📁</span>
                        <span>{proj.project_name}</span>
                      </button>
                      {open && (
                        <div className={styles.projectExpander__body}>
                          {(proj.users || []).map((u) => (
                            <div key={u.username} className={styles.userRow}>
                              <p className={styles.userRow__name}>
                                <span className={styles.userRow__icon} aria-hidden="true">👤</span>
                                {u.username} — {u.block_count} block(s)
                              </p>
                              <div className={styles.userRow__badges}>
                                {Object.entries(u.state_counts || {})
                                  .sort(([a], [b]) => a.localeCompare(b))
                                  .map(([st, n]) => (
                                    <span key={st} className={styles.userRow__badgeItem}>
                                      <WorkflowStatusBadge state={st} />
                                      <span className={styles.userRow__count}>{n}</span>
                                    </span>
                                  ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </section>
          </>
        )}
      </div>
    </PageContainer>
  );
}
