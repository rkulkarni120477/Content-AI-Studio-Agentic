/**
 * Agent Builder API Service
 * Handles all agent-related HTTP requests
 */

import axios from 'axios';
import type {
  Agent,
  AgentListItem,
  AgentCreateRequest,
  AgentUpdateRequest,
  AgentRun,
  AgentRunListItem,
  PaginatedResponse,
  BudgetStatus,
  UsageResponse,
  AgentRunStep,
  AgentCheckpoint,
} from '@types/agent';

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
// Agent Management API
// ============================================================================

/**
 * Create a new agent
 */
export async function createAgent(
  projectId: number,
  data: AgentCreateRequest,
): Promise<Agent> {
  const response = await axiosInstance.post(
    `/agents`,
    { ...data, project_id: projectId },
  );
  return response.data;
}

/**
 * Get all agents for a project
 */
export async function getAgents(
  projectId: number,
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
): Promise<PaginatedResponse<AgentListItem>> {
  const response = await axiosInstance.get(
    `/agents`,
    {
      params: {
        project_id: projectId,
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
 * Get a specific agent by ID
 */
export async function getAgent(
  projectId: number,
  agentId: number,
): Promise<Agent> {
  const response = await axiosInstance.get(
    `/agents/${agentId}`,
    { params: { project_id: projectId } },
  );
  return response.data;
}

/**
 * Update an agent
 */
export async function updateAgent(
  projectId: number,
  agentId: number,
  data: AgentUpdateRequest,
): Promise<Agent> {
  const response = await axiosInstance.put(
    `/agents/${agentId}`,
    { ...data, project_id: projectId },
  );
  return response.data;
}

/**
 * Delete/archive an agent
 */
export async function deleteAgent(
  projectId: number,
  agentId: number,
): Promise<{ message: string }> {
  const response = await axiosInstance.delete(
    `/agents/${agentId}`,
    { params: { project_id: projectId } },
  );
  return response.data;
}

// ============================================================================
// Agent Execution API
// ============================================================================

/**
 * Create and start a new agent run
 */
export async function createAgentRun(
  projectId: number,
  agentId: number,
  data: {
    input_content: string;
    input_context?: Record<string, any>;
    artifact_id?: number;
    artifact_type?: string;
    request_id?: string;
  },
): Promise<AgentRun> {
  const response = await axiosInstance.post(
    `/agents/${agentId}/runs`,
    { ...data, project_id: projectId },
  );
  return response.data;
}

/**
 * Get all runs for an agent
 */
export async function getAgentRuns(
  projectId: number,
  agentId: number,
  {
    page = 1,
    pageSize = 20,
    status,
  }: {
    page?: number;
    pageSize?: number;
    status?: string;
  } = {},
): Promise<PaginatedResponse<AgentRunListItem>> {
  const response = await axiosInstance.get(
    `/projects/${projectId}/agents/${agentId}/runs`,
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
 * Get a specific run details
 */
export async function getAgentRunDetails(
  projectId: number,
  agentId: number,
  runId: number,
): Promise<AgentRun> {
  const response = await axiosInstance.get(
    `/projects/${projectId}/agents/${agentId}/runs/${runId}`,
  );
  return response.data;
}

/**
 * Cancel a running agent
 */
export async function cancelAgentRun(
  projectId: number,
  agentId: number,
  runId: number,
): Promise<{ message: string }> {
  const response = await axiosInstance.post(
    `/projects/${projectId}/agents/${agentId}/runs/${runId}/cancel`,
  );
  return response.data;
}

/**
 * Get steps for a run
 */
export async function getAgentRunSteps(
  projectId: number,
  agentId: number,
  runId: number,
): Promise<AgentRunStep[]> {
  const response = await axiosInstance.get(
    `/projects/${projectId}/agents/${agentId}/runs/${runId}/steps`,
  );
  return response.data.items || response.data;
}

/**
 * Get checkpoints for a run
 */
export async function getAgentRunCheckpoints(
  projectId: number,
  agentId: number,
  runId: number,
  { page = 1, pageSize = 20 }: { page?: number; pageSize?: number } = {},
): Promise<PaginatedResponse<AgentCheckpoint>> {
  const response = await axiosInstance.get(
    `/projects/${projectId}/agents/${agentId}/runs/${runId}/checkpoints`,
    {
      params: { page, page_size: pageSize },
    },
  );
  return response.data;
}

/**
 * Resume a run from checkpoint
 */
export async function resumeFromCheckpoint(
  projectId: number,
  agentId: number,
  runId: number,
  checkpointId: number,
): Promise<AgentRun> {
  const response = await axiosInstance.post(
    `/projects/${projectId}/agents/${agentId}/runs/${runId}/resume`,
    { checkpoint_id: checkpointId },
  );
  return response.data;
}

// ============================================================================
// Budget & Analytics API
// ============================================================================

/**
 * Get current budget status for a project
 */
export async function getBudgetStatus(projectId: number): Promise<BudgetStatus> {
  const response = await axiosInstance.get(
    `/projects/${projectId}/agents/budget/status`,
  );
  return response.data;
}

/**
 * Get usage summary for a project
 */
export async function getUsageSummary(projectId: number): Promise<UsageResponse> {
  const response = await axiosInstance.get(
    `/projects/${projectId}/agents/budget/usage`,
  );
  return response.data;
}

/**
 * Get cost breakdown for a specific run
 */
export async function getRunCosts(
  projectId: number,
  agentId: number,
  runId: number,
): Promise<any> {
  const response = await axiosInstance.get(
    `/projects/${projectId}/agents/${agentId}/runs/${runId}/costs`,
  );
  return response.data;
}

export const agentApi = {
  createAgent,
  getAgents,
  getAgent,
  updateAgent,
  deleteAgent,
  createAgentRun,
  getAgentRuns,
  getAgentRunDetails,
  cancelAgentRun,
  getAgentRunSteps,
  getAgentRunCheckpoints,
  resumeFromCheckpoint,
  getBudgetStatus,
  getUsageSummary,
  getRunCosts,
};
