/**
 * CreateWorkflowPage
 * Multi-step workflow builder
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { useWorkflows } from '@hooks/useWorkflows';
import { useAgents } from '@hooks/useAgents';
import Button from '@components/common/Button/Button';
import WorkflowDiagram from '@components/AgentBuilder/WorkflowDiagram';
import RoleGate from '@components/AgentBuilder/RoleGate';
import Loader from '@components/common/Loader/Loader';
import styles from './CreateWorkflowPage.module.scss';

export function CreateWorkflowPage() {
  const navigate = useNavigate();
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;

  const [name, setName] = useState('');
  const [callHandle, setCallHandle] = useState('');
  const [description, setDescription] = useState('');
  const [selectedAgents, setSelectedAgents] = useState<Array<{ agentId: number; stepNumber: number }>>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { agents, loading: agentsLoading } = useAgents({ projectId, pageSize: 1000 });
  const { createWorkflow } = useWorkflows({ projectId });

  const handleAddAgent = (agentId: number) => {
    if (!selectedAgents.find((s) => s.agentId === agentId)) {
      setSelectedAgents([
        ...selectedAgents,
        { agentId, stepNumber: selectedAgents.length + 1 },
      ]);
    }
  };

  const handleRemoveAgent = (agentId: number) => {
    const updated = selectedAgents.filter((s) => s.agentId !== agentId);
    // Renumber steps
    const renumbered = updated.map((s, idx) => ({
      ...s,
      stepNumber: idx + 1,
    }));
    setSelectedAgents(renumbered);
  };

  const handleSubmit = async () => {
    if (!name.trim()) {
      alert('Please enter a workflow name');
      return;
    }
    if (!callHandle.trim()) {
      alert('Please enter a call handle');
      return;
    }
    if (selectedAgents.length === 0) {
      alert('Please add at least one agent to the workflow');
      return;
    }

    setIsSubmitting(true);
    try {
      const agentMap = new Map();
      agents.forEach((a) => {
        agentMap.set(a.id, a.name);
      });

      const steps = selectedAgents.map((s) => ({
        agent_id: s.agentId,
        agent_name: agentMap.get(s.agentId) || `Agent ${s.agentId}`,
        step_number: s.stepNumber,
      }));

      await createWorkflow({
        name,
        call_handle: callHandle,
        description,
        definition: {
          steps,
          variables: {},
          metadata: {},
        },
      });

      navigate('/agent-builder/workflows');
    } catch (err) {
      // Error is handled by hook
    } finally {
      setIsSubmitting(false);
    }
  };

  if (agentsLoading) {
    return <Loader size="lg" overlay />;
  }

  return (
    <RoleGate requiredRole={['admin', 'author']}>
      <div className={styles.page}>
        <div className={styles.header}>
          <Button
            variant="secondary"
            onClick={() => navigate(-1)}
            className={styles.header__back}
          >
            ← Back
          </Button>
          <h1 className={styles.title}>Create Workflow</h1>
        </div>

        <div className={styles.container}>
          <div className={styles.form}>
            <h2 className={styles.section__title}>Workflow Details</h2>

            <div className={styles.form__group}>
              <label className={styles.form__label}>Name *</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Content Processing Pipeline"
                className={styles.form__input}
                disabled={isSubmitting}
              />
            </div>

            <div className={styles.form__group}>
              <label className={styles.form__label}>Call Handle *</label>
              <input
                type="text"
                value={callHandle}
                onChange={(e) => setCallHandle(e.target.value)}
                placeholder="e.g., content-pipeline"
                className={styles.form__input}
                disabled={isSubmitting}
              />
            </div>

            <div className={styles.form__group}>
              <label className={styles.form__label}>Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe what this workflow does..."
                className={styles.form__textarea}
                rows={4}
                disabled={isSubmitting}
              />
            </div>

            <h2 className={styles.section__title}>Select Agents</h2>
            <p className={styles.section__help}>
              Add agents to your workflow in the order they should execute
            </p>

            <div className={styles.agentsList}>
              {agents.map((agent) => {
                const isSelected = selectedAgents.some((s) => s.agentId === agent.id);
                return (
                  <div
                    key={agent.id}
                    className={`${styles.agentItem} ${isSelected ? styles['agentItem--selected'] : ''}`}
                  >
                    <input
                      type="checkbox"
                      id={`agent-${agent.id}`}
                      checked={isSelected}
                      onChange={(e) => {
                        if (e.target.checked) {
                          handleAddAgent(agent.id);
                        } else {
                          handleRemoveAgent(agent.id);
                        }
                      }}
                      disabled={isSubmitting}
                    />
                    <label htmlFor={`agent-${agent.id}`} className={styles.agentLabel}>
                      <strong>{agent.name}</strong>
                      <span className={styles.agentHandle}>{agent.call_handle}</span>
                    </label>
                  </div>
                );
              })}
            </div>

            <div className={styles.form__actions}>
              <Button
                variant="primary"
                size="lg"
                onClick={handleSubmit}
                loading={isSubmitting}
                disabled={selectedAgents.length === 0}
              >
                Create Workflow
              </Button>
            </div>
          </div>

          <div className={styles.preview}>
            <h2 className={styles.section__title}>Workflow Preview</h2>
            {selectedAgents.length > 0 ? (
              <WorkflowDiagram
                definition={{
                  steps: selectedAgents.map((s) => ({
                    agent_id: s.agentId,
                    agent_name: agents.find((a) => a.id === s.agentId)?.name || `Agent ${s.agentId}`,
                    step_number: s.stepNumber,
                  })),
                }}
              />
            ) : (
              <div className={styles.preview__empty}>
                Select agents to see workflow preview
              </div>
            )}
          </div>
        </div>
      </div>
    </RoleGate>
  );
}

export default CreateWorkflowPage;
