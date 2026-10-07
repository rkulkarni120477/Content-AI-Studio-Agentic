/**
 * AgentBuilderContext
 * Global context for Agent Builder state
 */

import React, { createContext, useContext, useState, useCallback } from 'react';
import type { Agent, AgentRun } from '@types/agent';
import type { Workflow, WorkflowRun } from '@types/workflow';

interface AgentBuilderContextType {
  // Selected state
  selectedAgent: Agent | null;
  setSelectedAgent: (agent: Agent | null) => void;
  selectedWorkflow: Workflow | null;
  setSelectedWorkflow: (workflow: Workflow | null) => void;
  selectedRun: AgentRun | null;
  setSelectedRun: (run: AgentRun | null) => void;
  selectedWorkflowRun: WorkflowRun | null;
  setSelectedWorkflowRun: (run: WorkflowRun | null) => void;

  // Filters
  agentFilter: {
    state?: string;
    search?: string;
  };
  setAgentFilter: (filter: { state?: string; search?: string }) => void;

  workflowFilter: {
    state?: string;
    search?: string;
  };
  setWorkflowFilter: (filter: { state?: string; search?: string }) => void;

  // UI state
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

const AgentBuilderContext = createContext<AgentBuilderContextType | undefined>(undefined);

/**
 * Provider component for Agent Builder context
 */
export function AgentBuilderProvider({ children }: { children: React.ReactNode }) {
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const [selectedRun, setSelectedRun] = useState<AgentRun | null>(null);
  const [selectedWorkflowRun, setSelectedWorkflowRun] = useState<WorkflowRun | null>(null);

  const [agentFilter, setAgentFilter] = useState({
    state: undefined as string | undefined,
    search: undefined as string | undefined,
  });

  const [workflowFilter, setWorkflowFilter] = useState({
    state: undefined as string | undefined,
    search: undefined as string | undefined,
  });

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const value: AgentBuilderContextType = {
    selectedAgent,
    setSelectedAgent,
    selectedWorkflow,
    setSelectedWorkflow,
    selectedRun,
    setSelectedRun,
    selectedWorkflowRun,
    setSelectedWorkflowRun,
    agentFilter,
    setAgentFilter,
    workflowFilter,
    setWorkflowFilter,
    sidebarCollapsed,
    setSidebarCollapsed,
  };

  return (
    <AgentBuilderContext.Provider value={value}>
      {children}
    </AgentBuilderContext.Provider>
  );
}

/**
 * Hook to use Agent Builder context
 */
export function useAgentBuilder(): AgentBuilderContextType {
  const context = useContext(AgentBuilderContext);
  if (!context) {
    throw new Error('useAgentBuilder must be used within AgentBuilderProvider');
  }
  return context;
}

export default AgentBuilderContext;
