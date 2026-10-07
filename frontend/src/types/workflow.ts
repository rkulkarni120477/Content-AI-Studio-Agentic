/**
 * Type definitions for Workflow Builder
 */

import type { AgentListItem, TokenUsage, CostInfo } from './agent';

export type WorkflowStatus = 'draft' | 'active' | 'paused' | 'archived';
export type WorkflowRunStatus = 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
export type StepStatus = 'pending' | 'waiting' | 'running' | 'completed' | 'failed' | 'skipped';

export interface AgentStep {
  agent_id: number;
  agent_name: string;
  step_number: number;
  timeout_seconds?: number;
  input_mapping?: Record<string, string>; // Maps workflow variables to agent inputs
  output_mapping?: Record<string, string>; // Maps agent outputs to workflow variables
  handoff_condition?: string; // Optional condition for handoff
}

export interface WorkflowDefinition {
  steps: AgentStep[];
  variables?: Record<string, any>;
  metadata?: Record<string, any>;
}

export interface Workflow {
  id: number;
  project_id: number;
  name: string;
  call_handle: string;
  description?: string;
  definition: WorkflowDefinition;
  lifecycle_state: WorkflowStatus;
  owner: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowListItem {
  id: number;
  name: string;
  call_handle: string;
  lifecycle_state: WorkflowStatus;
  owner: string;
  agent_count: number;
  created_at: string;
}

export interface WorkflowCreateRequest {
  name: string;
  call_handle: string;
  description?: string;
  definition: WorkflowDefinition;
}

export interface WorkflowUpdateRequest {
  name?: string;
  description?: string;
  definition?: WorkflowDefinition;
  lifecycle_state?: WorkflowStatus;
}

export interface WorkflowExecutionInput {
  initial_input: string;
  variables?: Record<string, any>;
  context?: Record<string, any>;
}

export interface WorkflowStepExecution {
  step_number: number;
  agent_id: number;
  agent_name: string;
  status: StepStatus;
  input_data?: Record<string, any>;
  output_data?: Record<string, any>;
  error_message?: string;
  duration_ms?: number;
  token_usage?: TokenUsage;
  cost?: CostInfo;
  started_at?: string;
  completed_at?: string;
}

export interface WorkflowRun {
  id: number;
  project_id: number;
  workflow_id: number;
  status: WorkflowRunStatus;
  initiated_by: string;
  initial_input: string;
  current_step_number: number;
  total_steps: number;
  steps: WorkflowStepExecution[];
  final_output?: string;
  error_message?: string;
  total_token_usage?: TokenUsage;
  total_cost?: CostInfo;
  queued_at: string;
  started_at?: string;
  paused_at?: string;
  completed_at?: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowRunListItem {
  id: number;
  workflow_id: number;
  status: WorkflowRunStatus;
  initiated_by: string;
  current_step_number: number;
  total_steps: number;
  total_tokens?: number;
  total_cost?: number;
  completed_at?: string;
  created_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface MessageResponse {
  message: string;
  detail?: string;
}
