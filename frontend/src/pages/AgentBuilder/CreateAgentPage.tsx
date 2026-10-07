/**
 * CreateAgentPage
 * Multi-step form for creating new agents
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { useAgents } from '@hooks/useAgents';
import AgentForm from '@components/AgentBuilder/AgentForm';
import Button from '@components/common/Button/Button';
import RoleGate from '@components/AgentBuilder/RoleGate';
import styles from './CreateAgentPage.module.scss';

export function CreateAgentPage() {
  const navigate = useNavigate();
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { createAgent } = useAgents({ projectId });

  const handleSubmit = async (data: any) => {
    setIsSubmitting(true);
    try {
      const newAgent = await createAgent(data);
      navigate(`/agent-builder/agents/${newAgent.id}`);
    } catch (err) {
      // Error is handled by hook
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <RoleGate requiredRole={['admin', 'author']}>
      <div className={styles.page}>
        <div className={styles.header}>
          <div>
            <Button
              variant="secondary"
              onClick={() => navigate(-1)}
              className={styles.header__back}
            >
              ← Back
            </Button>
            <h1 className={styles.title}>Create Agent</h1>
            <p className={styles.subtitle}>
              Set up a new AI agent for your project
            </p>
          </div>
        </div>

        <div className={styles.container}>
          <div className={styles.card}>
            <AgentForm
              isLoading={isSubmitting}
              onSubmit={handleSubmit}
              templates={[
                { id: 1, name: 'Content Analyzer' },
                { id: 2, name: 'Content Generator' },
                { id: 3, name: 'Content Summarizer' },
                { id: 4, name: 'Content Translator' },
              ]}
            />
          </div>

          <div className={styles.sidebar}>
            <div className={styles.info}>
              <h3 className={styles.info__title}>Tips</h3>
              <ul className={styles.info__list}>
                <li>Choose a descriptive name for your agent</li>
                <li>Select a template that matches your use case</li>
                <li>Configure the model and parameters for your needs</li>
                <li>Start with default settings and fine-tune as needed</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </RoleGate>
  );
}

export default CreateAgentPage;
