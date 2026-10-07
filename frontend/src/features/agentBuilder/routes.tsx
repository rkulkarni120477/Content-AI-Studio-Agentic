/**
 * Agent Builder Routes
 * Defines all routes for the Agent Builder feature
 */

import React from 'react';
import { Navigate } from 'react-router-dom';
import { lazyWithReload } from '@utils/lazyWithReload';
import ProtectedRoute from '@components/layout/ProtectedRoute/ProtectedRoute';
import Loader from '@components/common/Loader/Loader';

const AgentBuilderLayout = lazyWithReload(
  () => import('@pages/AgentBuilder/AgentBuilderLayout'),
);
const AgentListPage = lazyWithReload(
  () => import('@pages/AgentBuilder/AgentListPage'),
);
const CreateAgentPage = lazyWithReload(
  () => import('@pages/AgentBuilder/CreateAgentPage'),
);
const EditAgentPage = lazyWithReload(
  () => import('@pages/AgentBuilder/EditAgentPage'),
);
const TestAgentPage = lazyWithReload(
  () => import('@pages/AgentBuilder/TestAgentPage'),
);
const WorkflowListPage = lazyWithReload(
  () => import('@pages/AgentBuilder/WorkflowListPage'),
);
const CreateWorkflowPage = lazyWithReload(
  () => import('@pages/AgentBuilder/CreateWorkflowPage'),
);
const ExecuteWorkflowPage = lazyWithReload(
  () => import('@pages/AgentBuilder/ExecuteWorkflowPage'),
);
const ResultsPage = lazyWithReload(
  () => import('@pages/AgentBuilder/ResultsPage'),
);
const BudgetPage = lazyWithReload(
  () => import('@pages/AgentBuilder/BudgetPage'),
);

const wrap = (comp: React.ReactElement) => (
  <React.Suspense fallback={<Loader size="xl" overlay />}>
    {comp}
  </React.Suspense>
);

export const agentBuilderRoutes = {
  path: '/agent-builder',
  element: (
    <ProtectedRoute>
      {wrap(<AgentBuilderLayout />)}
    </ProtectedRoute>
  ),
  children: [
    {
      index: true,
      element: <Navigate to="agents" replace />,
    },
    // Agent Routes
    {
      path: 'agents',
      element: wrap(<AgentListPage />),
    },
    {
      path: 'agents/create',
      element: wrap(<CreateAgentPage />),
    },
    {
      path: 'agents/:agentId/edit',
      element: wrap(<EditAgentPage />),
    },
    {
      path: 'agents/:agentId/test',
      element: wrap(<TestAgentPage />),
    },
    // Workflow Routes
    {
      path: 'workflows',
      element: wrap(<WorkflowListPage />),
    },
    {
      path: 'workflows/create',
      element: wrap(<CreateWorkflowPage />),
    },
    {
      path: 'workflows/:workflowId/execute',
      element: wrap(<ExecuteWorkflowPage />),
    },
    // Results & Analytics Routes
    {
      path: 'results/history',
      element: wrap(<ResultsPage />),
    },
    {
      path: 'results/analytics',
      element: wrap(<ResultsPage />),
    },
    {
      path: 'budget',
      element: wrap(<BudgetPage />),
    },
  ],
};

export default agentBuilderRoutes;
