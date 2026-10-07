/**
 * Custom hook for workflow operations
 * Provides state management and API integration for workflows
 */

import { useState, useCallback, useEffect } from 'react';
import toast from 'react-hot-toast';
import { workflowApi } from '@services/workflowApi';
import { extractErrorMessage } from '@utils/helpers';
import type {
  Workflow,
  WorkflowListItem,
  WorkflowCreateRequest,
  WorkflowUpdateRequest,
  WorkflowRun,
  WorkflowExecutionInput,
  PaginatedResponse,
} from '@types/workflow';

interface UseWorkflowsOptions {
  projectId: number;
  initialPage?: number;
  pageSize?: number;
}

export function useWorkflows({ projectId, initialPage = 1, pageSize = 20 }: UseWorkflowsOptions) {
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
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

  const fetchWorkflows = useCallback(async (page = initialPage, state?: string, search?: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await workflowApi.getWorkflows(projectId, {
        page,
        pageSize,
        state,
        search,
      });
      setWorkflows(response.items);
      setPagination({
        total: response.total,
        page: response.page,
        pageSize: response.page_size,
        totalPages: response.total_pages,
      });
      setFilters({ state, search });
    } catch (err) {
      const message = extractErrorMessage(err) || 'Failed to fetch workflows';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [projectId, pageSize, initialPage]);

  useEffect(() => {
    fetchWorkflows();
  }, [fetchWorkflows]);

  const createWorkflow = useCallback(async (data: WorkflowCreateRequest) => {
    setLoading(true);
    setError(null);
    try {
      const newWorkflow = await workflowApi.createWorkflow(projectId, data);
      toast.success(`Workflow "${newWorkflow.name}" created successfully`);
      await fetchWorkflows(1, filters.state, filters.search);
      return newWorkflow;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create workflow';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchWorkflows, filters]);

  const updateWorkflow = useCallback(async (workflowId: number, data: WorkflowUpdateRequest) => {
    setLoading(true);
    setError(null);
    try {
      const updated = await workflowApi.updateWorkflow(projectId, workflowId, data);
      toast.success('Workflow updated successfully');
      await fetchWorkflows(pagination.page, filters.state, filters.search);
      return updated;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update workflow';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchWorkflows, pagination.page, filters]);

  const deleteWorkflow = useCallback(async (workflowId: number) => {
    setLoading(true);
    setError(null);
    try {
      await workflowApi.deleteWorkflow(projectId, workflowId);
      toast.success('Workflow archived successfully');
      await fetchWorkflows(1, filters.state, filters.search);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete workflow';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchWorkflows, filters]);

  const changePage = useCallback((newPage: number) => {
    fetchWorkflows(newPage, filters.state, filters.search);
  }, [fetchWorkflows, filters]);

  const search = useCallback((query: string) => {
    fetchWorkflows(1, filters.state, query || undefined);
  }, [fetchWorkflows, filters]);

  const filterByState = useCallback((state: string | undefined) => {
    fetchWorkflows(1, state, filters.search);
  }, [fetchWorkflows, filters]);

  return {
    workflows,
    loading,
    error,
    pagination,
    filters,
    createWorkflow,
    updateWorkflow,
    deleteWorkflow,
    changePage,
    search,
    filterByState,
    refetch: () => fetchWorkflows(pagination.page, filters.state, filters.search),
  };
}

interface UseWorkflowDetailsOptions {
  projectId: number;
  workflowId: number;
}

export function useWorkflowDetails({ projectId, workflowId }: UseWorkflowDetailsOptions) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWorkflow = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await workflowApi.getWorkflow(projectId, workflowId);
      setWorkflow(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch workflow';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [projectId, workflowId]);

  useEffect(() => {
    if (workflowId) {
      fetchWorkflow();
    }
  }, [workflowId, fetchWorkflow]);

  return {
    workflow,
    loading,
    error,
    refetch: fetchWorkflow,
  };
}

interface UseWorkflowRunsOptions {
  projectId: number;
  workflowId: number;
  pageSize?: number;
}

export function useWorkflowRuns({ projectId, workflowId, pageSize = 20 }: UseWorkflowRunsOptions) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
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
      const response = await workflowApi.getWorkflowRuns(projectId, workflowId, {
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
  }, [projectId, workflowId, pageSize]);

  useEffect(() => {
    if (workflowId) {
      fetchRuns(1);
    }
  }, [workflowId, fetchRuns]);

  const executeWorkflow = useCallback(async (input: WorkflowExecutionInput) => {
    setLoading(true);
    setError(null);
    try {
      const run = await workflowApi.executeWorkflow(projectId, workflowId, input);
      toast.success('Workflow execution started');
      await fetchRuns(1);
      return run;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to execute workflow';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, workflowId, fetchRuns]);

  const pauseRun = useCallback(async (runId: number) => {
    setLoading(true);
    setError(null);
    try {
      await workflowApi.pauseWorkflowRun(projectId, workflowId, runId);
      toast.success('Workflow paused');
      await fetchRuns(pagination.page);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to pause workflow';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, workflowId, fetchRuns, pagination.page]);

  const resumeRun = useCallback(async (runId: number) => {
    setLoading(true);
    setError(null);
    try {
      await workflowApi.resumeWorkflowRun(projectId, workflowId, runId);
      toast.success('Workflow resumed');
      await fetchRuns(pagination.page);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to resume workflow';
      setError(message);
      toast.error(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, workflowId, fetchRuns, pagination.page]);

  return {
    runs,
    loading,
    error,
    pagination,
    executeWorkflow,
    pauseRun,
    resumeRun,
    changePage: (page: number) => fetchRuns(page),
    refetch: () => fetchRuns(pagination.page),
  };
}
