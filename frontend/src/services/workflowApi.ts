/**
 * Workflow API Service
 * Handles all workflow-related HTTP requests
 */

import axios from 'axios';
import type {
  Workflow,
  WorkflowListItem,
  WorkflowCreateRequest,
  WorkflowUpdateRequest,
  WorkflowRun,
  WorkflowRunListItem,
  WorkflowExecutionInput,
  PaginatedResponse,
} from '@types/workflow';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const API_BASE = `${API_BASE_URL}/api/v1`;

const axiosInstance = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
});

// Add auth token to requests
axiosInstance.interceptors.request.use((config) => {
  const token = localStorage.getItem('content_ai_jwt');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ============================================================================
// Workflow Management API
// ============================================================================

/**
 * Create a new workflow
 */
export async function createWorkflow(
  _projectId: number,
  data: WorkflowCreateRequest,
): Promise<Workflow> {
  const response = await axiosInstance.post(
    `/workflows`,
    data,
  );
  return response.data;
}

/**
 * Get all workflows for a project
 */
export async function getWorkflows(
  _projectId: number,
  {
    page = 1,
    pageSize = 20,
    state,
    search,
  }: {
    page?: number;
    pageSize?: number;
    state?: string;
    search?: string;
  } = {},
): Promise<PaginatedResponse<WorkflowListItem>> {
  const response = await axiosInstance.get(
    `/workflows`,
    {
      params: {
        page,
        page_size: pageSize,
        ...(state && { state }),
        ...(search && { search }),
      },
    },
  );
  return response.data;
}

/**
 * Get a specific workflow by ID
 */
export async function getWorkflow(
  _projectId: number,
  workflowId: number,
): Promise<Workflow> {
  const response = await axiosInstance.get(
    `/workflows/${workflowId}`,
  );
  return response.data;
}

/**
 * Update a workflow
 */
export async function updateWorkflow(
  _projectId: number,
  workflowId: number,
  data: WorkflowUpdateRequest,
): Promise<Workflow> {
  const response = await axiosInstance.put(
    `/workflows/${workflowId}`,
    data,
  );
  return response.data;
}

/**
 * Delete/archive a workflow
 */
export async function deleteWorkflow(
  _projectId: number,
  workflowId: number,
): Promise<{ message: string }> {
  const response = await axiosInstance.delete(
    `/workflows/${workflowId}`,
  );
  return response.data;
}

// ============================================================================
// Workflow Execution API
// ============================================================================

/**
 * Execute a workflow
 */
export async function executeWorkflow(
  _projectId: number,
  workflowId: number,
  data: WorkflowExecutionInput,
): Promise<WorkflowRun> {
  const response = await axiosInstance.post(
    `/workflows/${workflowId}/execute`,
    data,
  );
  return response.data;
}

/**
 * Get all runs for a workflow
 */
export async function getWorkflowRuns(
  _projectId: number,
  workflowId: number,
  {
    page = 1,
    pageSize = 20,
    status,
  }: {
    page?: number;
    pageSize?: number;
    status?: string;
  } = {},
): Promise<PaginatedResponse<WorkflowRunListItem>> {
  const response = await axiosInstance.get(
    `/workflows/${workflowId}/runs`,
    {
      params: {
        page,
        page_size: pageSize,
        ...(status && { status }),
      },
    },
  );
  return response.data;
}

/**
 * Get a specific workflow run
 */
export async function getWorkflowRun(
  _projectId: number,
  workflowId: number,
  runId: number,
): Promise<WorkflowRun> {
  const response = await axiosInstance.get(
    `/workflows/${workflowId}/runs/${runId}`,
  );
  return response.data;
}

/**
 * Pause a workflow run
 */
export async function pauseWorkflowRun(
  _projectId: number,
  workflowId: number,
  runId: number,
): Promise<{ message: string }> {
  const response = await axiosInstance.post(
    `/workflows/${workflowId}/runs/${runId}/pause`,
  );
  return response.data;
}

/**
 * Resume a workflow run
 */
export async function resumeWorkflowRun(
  _projectId: number,
  workflowId: number,
  runId: number,
): Promise<{ message: string }> {
  const response = await axiosInstance.post(
    `/workflows/${workflowId}/runs/${runId}/resume`,
  );
  return response.data;
}

/**
 * Complete a step in a workflow
 */
export async function completeWorkflowStep(
  _projectId: number,
  workflowId: number,
  runId: number,
  stepNumber: number,
  data?: Record<string, any>,
): Promise<{ message: string }> {
  const response = await axiosInstance.post(
    `/workflows/${workflowId}/runs/${runId}/complete-step`,
    { step_number: stepNumber, ...data },
  );
  return response.data;
}

export const workflowApi = {
  createWorkflow,
  getWorkflows,
  getWorkflow,
  updateWorkflow,
  deleteWorkflow,
  executeWorkflow,
  getWorkflowRuns,
  getWorkflowRun,
  pauseWorkflowRun,
  resumeWorkflowRun,
  completeWorkflowStep,
};
