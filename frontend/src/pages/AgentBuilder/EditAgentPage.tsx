/**
 * EditAgentPage
 * Edit existing agent configuration
 */

import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { useAgentDetails, useAgents } from '@hooks/useAgents';
import AgentForm from '@components/AgentBuilder/AgentForm';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import RoleGate from '@components/AgentBuilder/RoleGate';
import styles from './EditAgentPage.module.scss';

export function EditAgentPage() {
  const navigate = useNavigate();
  const { agentId } = useParams<{ agentId: string }>();
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;

  const agentIdNum = parseInt(agentId || '0', 10);
  const { agent, loading: agentLoading } = useAgentDetails({
    projectId,
    agentId: agentIdNum,
  });

  const { updateAgent } = useAgents({ projectId });
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (data: any) => {
    setIsSubmitting(true);
    try {
      await updateAgent(agentIdNum, data);
      navigate(`/agent-builder/agents/${agentIdNum}`);
    } catch (err) {
      // Error is handled by hook
    } finally {
      setIsSubmitting(false);
    }
  };

  if (agentLoading || !agent) {
    return (
      <div className={styles.page}>
        <Loader size="lg" overlay />
      </div>
    );
  }

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
            <h1 className={styles.title}>Edit Agent: {agent.name}</h1>
          </div>
        </div>

        <div className={styles.container}>
          <div className={styles.card}>
            <AgentForm
              agent={agent}
              isLoading={isSubmitting}
              onSubmit={handleSubmit}
            />
          </div>

          <div className={styles.sidebar}>
            <div className={styles.info}>
              <h3 className={styles.info__title}>Version Info</h3>
              <div className={styles.info__item}>
                <span className={styles.info__label}>Current Version:</span>
                <span className={styles.info__value}>{agent.current_version_number}</span>
              </div>
              <div className={styles.info__item}>
                <span className={styles.info__label}>Active Version:</span>
                <span className={styles.info__value}>
                  {agent.activated_version_number || 'None'}
                </span>
              </div>
              {agent.activated_at && (
                <div className={styles.info__item}>
                  <span className={styles.info__label}>Activated:</span>
                  <span className={styles.info__value}>
                    {new Date(agent.activated_at).toLocaleDateString()}
                  </span>
                </div>
              )}
            </div>

            <div className={styles.status}>
              <h3 className={styles.status__title}>Status</h3>
              <div className={`${styles.status__badge} ${styles[`status__badge--${agent.lifecycle_state}`]}`}>
                {agent.lifecycle_state}
              </div>
              {agent.lifecycle_reason && (
                <p className={styles.status__reason}>{agent.lifecycle_reason}</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </RoleGate>
  );
}

export default EditAgentPage;
