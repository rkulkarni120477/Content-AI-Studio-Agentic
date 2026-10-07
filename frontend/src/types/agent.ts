/**
 * Type definitions for Agent Builder
 */

export type AgentLifecycleState = 'draft' | 'active' | 'paused' | 'archived';
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type StepStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface AgentConfiguration {
  model_id?: string;
  temperature?: number;
  max_tokens?: number;
  additional_config?: Record<string, any>;
}

export interface Agent {
  id: number;
  project_id: number;
  template_id: number;
  name: string;
  call_handle: string;
  description?: string;
  owner: string;
  configuration: AgentConfiguration;
  lifecycle_state: AgentLifecycleState;
  lifecycle_reason?: string;
  current_version_number: number;
  activated_version_number?: number;
  activated_at?: string;
  created_at: string;
  updated_at: string;
}

export interface AgentListItem {
  id: number;
  name: string;
  call_handle: string;
  lifecycle_state: AgentLifecycleState;
  template_id: number;
  owner: string;
  current_version_number: number;
  created_at: string;
}

export interface AgentCreateRequest {
  name: string;
  template_id: number;
  call_handle: string;
  description?: string;
  configuration: AgentConfiguration;
}

export interface AgentUpdateRequest {
  name?: string;
  description?: string;
  configuration?: AgentConfiguration;
  lifecycle_state?: AgentLifecycleState;
  lifecycle_reason?: string;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface CostInfo {
  model_name: string;
  input_cost: number;
  output_cost: number;
  total_cost: number;
  currency: string;
}

export interface AgentRun {
  id: number;
  project_id: number;
  definition_id: number;
  version_id: number;
  initiated_by: string;
  status: RunStatus;
  input_content: string;
  input_context?: Record<string, any>;
  artifact_id?: number;
  artifact_type?: string;
  output_content?: string;
  error_message?: string;
  token_usage?: TokenUsage;
  cost?: CostInfo;
  queued_at: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  updated_at: string;
}

export interface AgentRunListItem {
  id: number;
  definition_id: number;
  status: RunStatus;
  input_summary: string;
  initiated_by: string;
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

export interface AgentRunStep {
  id: number;
  run_id: number;
  step_number: number;
  status: StepStatus;
  step_type: string;
  input_data?: Record<string, any>;
  output_data?: Record<string, any>;
  error_message?: string;
  duration_ms?: number;
  created_at: string;
  updated_at: string;
}

export interface AgentCheckpoint {
  id: number;
  run_id: number;
  checkpoint_number: number;
  state_snapshot: Record<string, any>;
  metadata?: Record<string, any>;
  created_at: string;
}

export interface BudgetStatus {
  project_id: number;
  total_budget: number;
  current_spend: number;
  remaining_budget: number;
  percentage_used: number;
  period_start: string;
  period_end: string;
  is_exceeded: boolean;
}

export interface UsageResponse {
  total_runs: number;
  completed_runs: number;
  failed_runs: number;
  total_tokens: number;
  total_cost: number;
  cost_by_model: Record<string, number>;
}
