/**
 * Custom hook for agent operations
 * Provides state management and API integration for agents
 */

import { useState, useCallback, useEffect } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import toast from 'react-hot-toast';
import { agentApi } from '@services/agentApi';
import type {
  Agent,
  AgentListItem,
  AgentCreateRequest,
  AgentUpdateRequest,
  AgentRun,
  PaginatedResponse,
  BudgetStatus,
} from '@types/agent';

interface UseAgentsOptions {
  projectId: number;
  initialPage?: number;
  pageSize?: number;
}

export function useAgents({ projectId, initialPage = 1, pageSize = 20 }: UseAgentsOptions) {
  const [agents, setAgents] = useState<AgentListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pagination, setPagination] = useState({
    total: 0,
    page: initialPage,
    pageSize,
    totalPages: 0,
  });

  const [filters, setFilters] = useState({
    state: undefined as string | undefined,
    search: undefined as string | undefined,
  });

  const fetchAgents = useCallback(async (page = initialPage, state?: string, search?: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await agentApi.getAgents(projectId, {
        page,
        pageSize,
        state,
        search,
      });
      setAgents(response.items);
      setPagination({
        total: response.total,
        page: response.page,
        pageSize: response.page_size,
        totalPages: response.total_pages,
      });
      setFilters({ state, search });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch agents';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [projectId, pageSize, initialPage]);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const createAgent = useCallback(async (data: AgentCreateRequest) => {
    setLoading(true);
    setError(null);
    try {
      const newAgent = await agentApi.createAgent(projectId, data);
      toast.success(`Agent "${newAgent.name}" created successfully`);
      await fetchAgents(1, filters.state, filters.search);
      return newAgent;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create agent';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchAgents, filters]);

  const updateAgent = useCallback(async (agentId: number, data: AgentUpdateRequest) => {
    setLoading(true);
    setError(null);
    try {
      const updated = await agentApi.updateAgent(projectId, agentId, data);
      toast.success('Agent updated successfully');
      await fetchAgents(pagination.page, filters.state, filters.search);
      return updated;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update agent';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchAgents, pagination.page, filters]);

  const deleteAgent = useCallback(async (agentId: number) => {
    setLoading(true);
    setError(null);
    try {
      await agentApi.deleteAgent(projectId, agentId);
      toast.success('Agent archived successfully');
      await fetchAgents(1, filters.state, filters.search);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete agent';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchAgents, filters]);

  const changePage = useCallback((newPage: number) => {
    fetchAgents(newPage, filters.state, filters.search);
  }, [fetchAgents, filters]);

  const search = useCallback((query: string) => {
    fetchAgents(1, filters.state, query || undefined);
  }, [fetchAgents, filters]);

  const filterByState = useCallback((state: string | undefined) => {
    fetchAgents(1, state, filters.search);
  }, [fetchAgents, filters]);

  return {
    agents,
    loading,
    error,
    pagination,
    filters,
    createAgent,
    updateAgent,
    deleteAgent,
    changePage,
    search,
    filterByState,
    refetch: () => fetchAgents(pagination.page, filters.state, filters.search),
  };
}

interface UseAgentDetailsOptions {
  projectId: number;
  agentId: number;
}

export function useAgentDetails({ projectId, agentId }: UseAgentDetailsOptions) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAgent = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await agentApi.getAgent(projectId, agentId);
      setAgent(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch agent';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [projectId, agentId]);

  useEffect(() => {
    if (agentId) {
      fetchAgent();
    }
  }, [agentId, fetchAgent]);

  return {
    agent,
    loading,
    error,
    refetch: fetchAgent,
  };
}

interface UseAgentRunsOptions {
  projectId: number;
  agentId: number;
  pageSize?: number;
}

export function useAgentRuns({ projectId, agentId, pageSize = 20 }: UseAgentRunsOptions) {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pagination, setPagination] = useState({
    total: 0,
    page: 1,
    pageSize,
    totalPages: 0,
  });

  const fetchRuns = useCallback(async (page = 1, status?: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await agentApi.getAgentRuns(projectId, agentId, {
        page,
        pageSize,
        status,
      });
      setRuns(response.items as any[]);
      setPagination({
        total: response.total,
        page: response.page,
        pageSize: response.page_size,
        totalPages: response.total_pages,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch runs';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [projectId, agentId, pageSize]);

  useEffect(() => {
    if (agentId) {
      fetchRuns(1);
    }
  }, [agentId, fetchRuns]);

  const createRun = useCallback(async (inputContent: string, context?: Record<string, any>) => {
    setLoading(true);
    setError(null);
    try {
      const run = await agentApi.createAgentRun(projectId, agentId, {
        input_content: inputContent,
        input_context: context,
      });
      toast.success('Agent run started');
      await fetchRuns(1);
      return run;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to start run';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, agentId, fetchRuns]);

  const cancelRun = useCallback(async (runId: number) => {
    setLoading(true);
    setError(null);
    try {
      await agentApi.cancelAgentRun(projectId, agentId, runId);
      toast.success('Run cancelled');
      await fetchRuns(pagination.page);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to cancel run';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, agentId, fetchRuns, pagination.page]);

  return {
    runs,
    loading,
    error,
    pagination,
    createRun,
    cancelRun,
    changePage: (page: number) => fetchRuns(page),
    refetch: () => fetchRuns(pagination.page),
  };
}

interface UseAgentBudgetOptions {
  projectId: number;
}

export function useAgentBudget({ projectId }: UseAgentBudgetOptions) {
  const [budget, setBudget] = useState<BudgetStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchBudget = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await agentApi.getBudgetStatus(projectId);
      setBudget(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch budget';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchBudget();
  }, [fetchBudget]);

  return {
    budget,
    loading,
    error,
    refetch: fetchBudget,
  };
}
