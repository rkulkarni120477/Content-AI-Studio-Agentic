# Agent Builder Frontend

Comprehensive React/TypeScript UI for the Agent Builder feature. Provides a complete interface for managing agents, workflows, and monitoring their execution with full RBAC support.

## Architecture Overview

```
Agent Builder Feature
├── Pages (12 main pages)
├── Components (9+ reusable components)
├── Services (API integration)
├── Hooks (State management)
├── Types (TypeScript definitions)
├── Context (Global state)
└── Routes (Navigation)
```

## File Structure

```
frontend/src/
├── types/
│   ├── agent.ts                 # Agent type definitions
│   └── workflow.ts              # Workflow type definitions
│
├── services/
│   ├── agentApi.ts             # Agent API service
│   └── workflowApi.ts          # Workflow API service
│
├── hooks/
│   ├── useAgents.ts            # Agent hooks
│   └── useWorkflows.ts         # Workflow hooks
│
├── components/AgentBuilder/
│   ├── RoleGate.tsx            # RBAC wrapper component
│   ├── PermissionCheck.tsx      # Permission checker component
│   ├── AgentCard.tsx           # Agent display card
│   ├── AgentForm.tsx           # Agent form component
│   ├── WorkflowDiagram.tsx     # Workflow visualization
│   ├── ExecutionMonitor.tsx    # Run progress monitor
│   ├── CostDisplay.tsx         # Cost breakdown display
│   ├── BudgetBar.tsx           # Budget progress bar
│   └── AgentBuilderNav.tsx     # Navigation menu
│
├── pages/AgentBuilder/
│   ├── AgentBuilderLayout.tsx      # Main layout
│   ├── AgentListPage.tsx           # Agent list page
│   ├── CreateAgentPage.tsx         # Create agent page
│   ├── EditAgentPage.tsx           # Edit agent page
│   ├── TestAgentPage.tsx           # Test agent page
│   ├── RunAgentPage.tsx            # Run agent page (future)
│   ├── AgentRunHistoryPage.tsx     # Run history page (future)
│   ├── WorkflowListPage.tsx        # Workflow list page
│   ├── CreateWorkflowPage.tsx      # Create workflow page
│   ├── ExecuteWorkflowPage.tsx     # Execute workflow page
│   ├── WorkflowRunDetailsPage.tsx  # Workflow run details (future)
│   ├── ResultsPage.tsx            # Results & analytics page
│   └── BudgetPage.tsx             # Budget tracking page
│
├── context/
│   └── AgentBuilderContext.tsx    # Global state context
│
└── features/agentBuilder/
    └── routes.tsx               # Route definitions
```

## Key Features

### 1. Agent Management
- **List Agents**: Display all agents with filtering by status
- **Create Agent**: Multi-step form for agent creation
- **Edit Agent**: Update agent configuration
- **Test Agent**: Test agents with sample input
- **Version Control**: Track and manage agent versions
- **Status Management**: Draft, Active, Paused, Archived states

### 2. Workflow Orchestration
- **Create Workflows**: Multi-agent workflow builder
- **Visual Diagram**: See workflow structure with agent steps
- **Execute Workflows**: Run multi-agent workflows
- **Handoff Handling**: Pass data between agents
- **Progress Tracking**: Monitor each agent in the workflow

### 3. Execution & Monitoring
- **Real-time Progress**: Monitor agent/workflow execution
- **Status Tracking**: Queued → Running → Completed/Failed states
- **Token Usage**: Track input/output tokens
- **Cost Tracking**: Show cost breakdown by model
- **Error Handling**: Display execution errors clearly

### 4. Budget & Analytics
- **Budget Tracking**: Monitor spend vs. budget limit
- **Color-coded Warnings**: Visual indicators for budget usage
- **Usage Analytics**: Total runs, tokens, costs per model
- **Cost Breakdown**: Analyze spending patterns
- **Time-series Data**: Track costs over time

### 5. Role-Based Access Control
- **Platform Admin**: Full access to all features
- **Tenant Admin**: Manage project resources
- **Author**: Create and test agents/workflows
- **Reviewer**: View runs and approve changes
- **User**: Run agents, view own results

## Component Details

### RoleGate Component
Wraps content to restrict visibility based on user role.

```tsx
<RoleGate requiredRole={['admin', 'author']}>
  <CreateAgentButton />
</RoleGate>
```

### ExecutionMonitor Component
Shows real-time progress of agent/workflow execution.

```tsx
<ExecutionMonitor
  status={run.status}
  progress={50}
  currentStep="Processing"
  elapsedTime={5000}
  onCancel={handleCancel}
/>
```

### CostDisplay Component
Shows token usage and cost breakdown.

```tsx
<CostDisplay
  tokenUsage={run.token_usage}
  cost={run.cost}
  compact={false}
/>
```

### BudgetBar Component
Visualizes budget usage with color-coded progress.

```tsx
<BudgetBar budget={budgetStatus} />
```

## Hooks

### useAgents
Manages agent list, creation, updates, and deletions.

```tsx
const {
  agents,
  loading,
  error,
  pagination,
  createAgent,
  updateAgent,
  deleteAgent,
  changePage,
  search,
  filterByState,
} = useAgents({ projectId });
```

### useAgentDetails
Fetches details for a specific agent.

```tsx
const { agent, loading, error, refetch } = useAgentDetails({
  projectId,
  agentId: 123,
});
```

### useAgentRuns
Manages agent run history and execution.

```tsx
const {
  runs,
  loading,
  error,
  createRun,
  cancelRun,
  changePage,
} = useAgentRuns({ projectId, agentId });
```

### useWorkflows
Manages workflow list and CRUD operations.

```tsx
const {
  workflows,
  loading,
  error,
  createWorkflow,
  updateWorkflow,
  deleteWorkflow,
} = useWorkflows({ projectId });
```

### useWorkflowRuns
Manages workflow execution and monitoring.

```tsx
const {
  runs,
  loading,
  executeWorkflow,
  pauseRun,
  resumeRun,
} = useWorkflowRuns({ projectId, workflowId });
```

### useAgentBudget
Fetches and monitors project budget status.

```tsx
const { budget, loading, error } = useAgentBudget({ projectId });
```

## API Integration

### Agent API Endpoints

```typescript
// Agent Management
createAgent(projectId, data)           // Create new agent
getAgents(projectId, options)          // List agents (paginated)
getAgent(projectId, agentId)           // Get agent details
updateAgent(projectId, agentId, data)  // Update agent
deleteAgent(projectId, agentId)        // Archive agent

// Agent Execution
createAgentRun(projectId, agentId, data)          // Start run
getAgentRuns(projectId, agentId, options)         // List runs
getAgentRunDetails(projectId, agentId, runId)     // Get run details
cancelAgentRun(projectId, agentId, runId)         // Cancel run
getAgentRunSteps(projectId, agentId, runId)       // Get steps
getAgentRunCheckpoints(projectId, agentId, runId) // Get checkpoints
resumeFromCheckpoint(projectId, agentId, runId, checkpointId)

// Budget & Analytics
getBudgetStatus(projectId)             // Current budget status
getUsageSummary(projectId)              // Usage summary
getRunCosts(projectId, agentId, runId)  // Run cost breakdown
```

### Workflow API Endpoints

```typescript
// Workflow Management
createWorkflow(projectId, data)           // Create workflow
getWorkflows(projectId, options)          // List workflows
getWorkflow(projectId, workflowId)        // Get workflow details
updateWorkflow(projectId, workflowId, data) // Update workflow
deleteWorkflow(projectId, workflowId)     // Archive workflow

// Workflow Execution
executeWorkflow(projectId, workflowId, data)       // Start execution
getWorkflowRuns(projectId, workflowId, options)    // List runs
getWorkflowRun(projectId, workflowId, runId)       // Get run details
pauseWorkflowRun(projectId, workflowId, runId)     // Pause workflow
resumeWorkflowRun(projectId, workflowId, runId)    // Resume workflow
completeWorkflowStep(projectId, workflowId, runId, stepNumber) // Complete step
```

## State Management

### Context-based Global State
The `AgentBuilderContext` provides:
- Selected agent/workflow
- Selected runs
- UI filters
- Sidebar state
- User preferences

### Hook-based Local State
Each page manages its own state through custom hooks:
- Loading states
- Error states
- Pagination
- Filtering
- Search

## Routes

```
/agent-builder
  /agents
    /create
    /:agentId/edit
    /:agentId/test
  /workflows
    /create
    /:workflowId/edit
    /:workflowId/execute
    /:workflowId/:runId/details
  /results
    /history
    /analytics
  /budget
```

## Styling

### CSS Modules
Each component and page has an associated `.module.scss` file:
- Component styles are scoped to prevent conflicts
- Dark mode support via CSS variables
- Responsive design with mobile-first approach
- Accessible color contrasts

### CSS Variables
Uses CSS custom properties for theming:
```scss
--color-primary: #007bff
--color-danger: #dc3545
--color-warning: #ffc107
--color-success: #28a745
--color-bg-primary: #fff
--color-bg-secondary: #f8f9fa
--color-text-primary: #000
--color-text-secondary: #666
--color-border: #e0e0e0
```

## Form Validation

Uses React Hook Form with Zod for validation:
```typescript
const schema = z.object({
  name: z.string().min(1),
  call_handle: z.string().regex(/^[a-z0-9_-]+$/),
  temperature: z.number().min(0).max(2),
});

const { register, formState: { errors } } = useForm({
  resolver: zodResolver(schema),
});
```

## Error Handling

- API errors shown in toast notifications
- Form validation errors displayed inline
- Fallback UI for permission denied
- Error boundaries for component crashes
- Loading states for async operations

## Accessibility

- Semantic HTML (nav, main, section, article)
- ARIA labels for interactive elements
- Keyboard navigation support
- Color-blind friendly palettes
- Focus management
- Screen reader support

## Performance Optimization

- Lazy loading of pages with `lazyWithReload`
- Suspense boundaries with loading indicator
- Pagination to limit API responses
- Debounced search input
- Memoized components (React.memo)
- Optimized re-renders with useCallback

## Testing (Future)

Should include:
- Unit tests for hooks
- Component tests with React Testing Library
- Integration tests for API flows
- E2E tests with Cypress
- Accessibility tests with axe

## Future Enhancements

1. **Advanced Filtering**: Date range, cost range, status combinations
2. **Batch Operations**: Bulk activate/pause/archive agents
3. **Templates**: Pre-configured agent templates
4. **Versioning UI**: Visual version diff and rollback
5. **Collaboration**: Comments and approvals on agents
6. **Scheduling**: Schedule agent runs
7. **Export/Import**: Share agent configurations
8. **API Keys**: Generate API keys for programmatic access
9. **Webhooks**: Trigger agents via webhooks
10. **Custom Metrics**: Track custom performance metrics

## Development Guidelines

### Adding a New Page

1. Create page file in `pages/AgentBuilder/`
2. Create corresponding `.module.scss`
3. Add route to `features/agentBuilder/routes.tsx`
4. Use custom hooks for data fetching
5. Handle loading/error states
6. Implement RBAC with RoleGate if needed

### Adding a New Component

1. Create component file in `components/AgentBuilder/`
2. Create corresponding `.module.scss`
3. Export component as named export
4. Add TypeScript types
5. Document props with JSDoc
6. Include examples in comments

### API Changes

If backend API changes:
1. Update types in `types/agent.ts` or `types/workflow.ts`
2. Update service methods in `services/agentApi.ts` or `services/workflowApi.ts`
3. Update hook to handle new fields
4. Update components that use the data

## Troubleshooting

### Common Issues

**Problem**: Pages not loading
- Check if routes are registered in main router
- Verify lazyWithReload imports are correct
- Check browser console for errors

**Problem**: API calls failing
- Check if API_BASE is configured correctly
- Verify auth token is in localStorage
- Check CORS settings in backend

**Problem**: Styles not applied
- Ensure `.module.scss` file exists
- Check CSS class names in component
- Verify CSS variables are defined
- Clear browser cache

**Problem**: Roles not working
- Check if user role is correctly set in auth state
- Verify RoleGate has correct requiredRole prop
- Check Redux store for auth state

## Dependencies

Core dependencies:
- `react@18`: UI library
- `react-router-dom@6`: Routing
- `react-hook-form@7`: Form handling
- `zod@3`: Schema validation
- `axios@1.7`: HTTP client
- `react-hot-toast@2.4`: Notifications
- `recharts@2.14`: Charts (for analytics)
- `date-fns@4`: Date utilities

## License

Same as parent project
