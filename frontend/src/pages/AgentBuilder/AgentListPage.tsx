/**
 * AgentListPage
 * Display and manage list of agents
 */

import React, { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { useAgents } from '@hooks/useAgents';
import AgentCard from '@components/AgentBuilder/AgentCard';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import RoleGate from '@components/AgentBuilder/RoleGate';
import styles from './AgentListPage.module.scss';

const PAGE_SIZE = 12;

export function AgentListPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { selectedProject } = useAppSelector((state) => state.dashboard);
  const queryProjectId = searchParams.get('project');
  const projectId = selectedProject?.id || (queryProjectId ? Number(queryProjectId) : 0);

  const [searchQuery, setSearchQuery] = useState('');
  const [stateFilter, setStateFilter] = useState<string | undefined>(undefined);

  const {
    agents,
    loading,
    error,
    pagination,
    filters,
    createAgent,
    deleteAgent,
    changePage,
    search,
    filterByState,
  } = useAgents({
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
    navigate('/agent-builder/agents/create');
  };

  const handleEdit = (agentId: number) => {
    navigate(`/agent-builder/agents/${agentId}/edit`);
  };

  const handleTest = (agentId: number) => {
    navigate(`/agent-builder/agents/${agentId}/test`);
  };

  const handleArchive = async (agentId: number) => {
    if (confirm('Are you sure you want to archive this agent?')) {
      try {
        await deleteAgent(agentId);
      } catch (err) {
        // Error is handled by hook
      }
    }
  };

  if (error && agents.length === 0) {
    return (
      <div className={styles.page}>
        <div className={styles.error}>
          <h2>Error loading agents</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>Agents</h1>
          <p className={styles.subtitle}>
            Manage and execute your AI agents
          </p>
        </div>
        <RoleGate requiredRole={['admin', 'author']}>
          <Button
            variant="primary"
            size="lg"
            onClick={handleCreate}
          >
            Create Agent
          </Button>
        </RoleGate>
      </div>

      <div className={styles.filters}>
        <div className={styles.filters__search}>
          <Input
            type="search"
            placeholder="Search agents..."
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

      {loading && agents.length === 0 ? (
        <div className={styles.loading}>
          <Loader size="lg" overlay />
        </div>
      ) : agents.length === 0 ? (
        <EmptyState
          title="No agents found"
          description="Create your first agent to get started"
          action={
            <RoleGate requiredRole={['admin', 'author']}>
              <Button variant="primary" onClick={handleCreate}>
                Create Agent
              </Button>
            </RoleGate>
          }
        />
      ) : (
        <>
          <div className={styles.grid}>
            {agents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                onEdit={handleEdit}
                onTest={handleTest}
                onArchive={handleArchive}
              />
            ))}
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
                {' '}
                ({pagination.total} total)
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

export default AgentListPage;
