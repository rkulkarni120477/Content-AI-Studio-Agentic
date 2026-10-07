/**
 * WorkflowListPage
 * Display and manage list of workflows
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { useWorkflows } from '@hooks/useWorkflows';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import RoleGate from '@components/AgentBuilder/RoleGate';
import styles from './WorkflowListPage.module.scss';

const PAGE_SIZE = 12;

export function WorkflowListPage() {
  const navigate = useNavigate();
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;

  const [searchQuery, setSearchQuery] = useState('');
  const [stateFilter, setStateFilter] = useState<string | undefined>(undefined);

  const {
    workflows,
    loading,
    error,
    pagination,
    filters,
    deleteWorkflow,
    changePage,
    search,
    filterByState,
  } = useWorkflows({
    projectId,
    pageSize: PAGE_SIZE,
  });

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    search(value);
  };

  const handleFilterChange = (state: string) => {
    setStateFilter(state);
    filterByState(state === 'all' ? undefined : state);
  };

  const handleCreate = () => {
    navigate('/agent-builder/workflows/create');
  };

  const handleEdit = (workflowId: number) => {
    navigate(`/agent-builder/workflows/${workflowId}/edit`);
  };

  const handleExecute = (workflowId: number) => {
    navigate(`/agent-builder/workflows/${workflowId}/execute`);
  };

  const handleArchive = async (workflowId: number) => {
    if (confirm('Are you sure you want to archive this workflow?')) {
      try {
        await deleteWorkflow(workflowId);
      } catch (err) {
        // Error handled by hook
      }
    }
  };

  if (error && workflows.length === 0) {
    return (
      <div className={styles.page}>
        <div className={styles.error}>
          <h2>Error loading workflows</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>Workflows</h1>
          <p className={styles.subtitle}>
            Create and execute multi-agent workflows
          </p>
        </div>
        <RoleGate requiredRole={['admin', 'author']}>
          <Button
            variant="primary"
            size="lg"
            onClick={handleCreate}
          >
            Create Workflow
          </Button>
        </RoleGate>
      </div>

      <div className={styles.filters}>
        <div className={styles.filters__search}>
          <Input
            type="search"
            placeholder="Search workflows..."
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            disabled={loading}
          />
        </div>

        <div className={styles.filters__state}>
          <select
            value={stateFilter || 'all'}
            onChange={(e) => handleFilterChange(e.target.value)}
            className={styles.filters__select}
            disabled={loading}
          >
            <option value="all">All States</option>
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
            <option value="archived">Archived</option>
          </select>
        </div>

        {(filters.search || filters.state) && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setSearchQuery('');
              setStateFilter(undefined);
              search('');
              filterByState(undefined);
            }}
          >
            Clear Filters
          </Button>
        )}
      </div>

      {loading && workflows.length === 0 ? (
        <div className={styles.loading}>
          <Loader size="lg" overlay />
        </div>
      ) : workflows.length === 0 ? (
        <EmptyState
          title="No workflows found"
          description="Create your first workflow to orchestrate multiple agents"
          action={
            <RoleGate requiredRole={['admin', 'author']}>
              <Button variant="primary" onClick={handleCreate}>
                Create Workflow
              </Button>
            </RoleGate>
          }
        />
      ) : (
        <>
          <div className={styles.table}>
            <table className={styles.table__content}>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Agents</th>
                  <th>Owner</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {workflows.map((workflow) => (
                  <tr key={workflow.id} className={styles.table__row}>
                    <td className={styles.table__cell}>
                      <strong>{workflow.name}</strong>
                    </td>
                    <td className={styles.table__cell}>
                      {workflow.agent_count}
                    </td>
                    <td className={styles.table__cell}>
                      {workflow.owner}
                    </td>
                    <td className={styles.table__cell}>
                      <span className={`${styles.status} ${styles[`status--${workflow.lifecycle_state}`]}`}>
                        {workflow.lifecycle_state}
                      </span>
                    </td>
                    <td className={styles.table__cell}>
                      {new Date(workflow.created_at).toLocaleDateString()}
                    </td>
                    <td className={styles.table__cell}>
                      <div className={styles.table__actions}>
                        <RoleGate requiredRole={['admin', 'author']}>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => handleEdit(workflow.id)}
                          >
                            Edit
                          </Button>
                        </RoleGate>
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={() => handleExecute(workflow.id)}
                        >
                          Execute
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {pagination.totalPages > 1 && (
            <div className={styles.pagination}>
              <button
                className={styles.pagination__btn}
                onClick={() => changePage(pagination.page - 1)}
                disabled={pagination.page === 1 || loading}
              >
                Previous
              </button>

              <div className={styles.pagination__info}>
                Page {pagination.page} of {pagination.totalPages}
              </div>

              <button
                className={styles.pagination__btn}
                onClick={() => changePage(pagination.page + 1)}
                disabled={pagination.page >= pagination.totalPages || loading}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default WorkflowListPage;
