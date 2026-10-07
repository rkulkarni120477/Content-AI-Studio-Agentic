/**
 * AgentCard Component
 * Displays agent summary with quick actions
 */

import React from 'react';
import { useNavigate } from 'react-router-dom';
import type { AgentListItem } from '@types/agent';
import Button from '@components/common/Button/Button';
import styles from './AgentCard.module.scss';

interface AgentCardProps {
  agent: AgentListItem;
  onEdit?: (agentId: number) => void;
  onTest?: (agentId: number) => void;
  onActivate?: (agentId: number) => void;
  onPause?: (agentId: number) => void;
  onArchive?: (agentId: number) => void;
  className?: string;
}

const statusColors: Record<string, string> = {
  draft: 'gray',
  active: 'green',
  paused: 'yellow',
  archived: 'red',
};

export function AgentCard({
  agent,
  onEdit,
  onTest,
  onActivate,
  onPause,
  onArchive,
  className,
}: AgentCardProps) {
  const navigate = useNavigate();
  const statusColor = statusColors[agent.lifecycle_state] || 'gray';

  const handleViewDetails = () => {
    navigate(`/agent-builder/agents/${agent.id}`);
  };

  return (
    <div className={`${styles.card} ${className || ''}`}>
      <div className={styles.card__header}>
        <div className={styles.card__title}>{agent.name}</div>
        <div className={`${styles.card__status} ${styles[`card__status--${statusColor}`]}`}>
          {agent.lifecycle_state}
        </div>
      </div>

      <div className={styles.card__body}>
        <div className={styles.card__info}>
          <div className={styles.card__infoRow}>
            <span className={styles.card__label}>Handle:</span>
            <code className={styles.card__value}>{agent.call_handle}</code>
          </div>
          <div className={styles.card__infoRow}>
            <span className={styles.card__label}>Owner:</span>
            <span className={styles.card__value}>{agent.owner}</span>
          </div>
          <div className={styles.card__infoRow}>
            <span className={styles.card__label}>Version:</span>
            <span className={styles.card__value}>{agent.current_version_number}</span>
          </div>
          <div className={styles.card__infoRow}>
            <span className={styles.card__label}>Created:</span>
            <span className={styles.card__value}>
              {new Date(agent.created_at).toLocaleDateString()}
            </span>
          </div>
        </div>
      </div>

      <div className={styles.card__footer}>
        <div className={styles.card__actions}>
          <Button
            size="sm"
            variant="secondary"
            onClick={handleViewDetails}
          >
            View
          </Button>

          {onEdit && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onEdit(agent.id)}
            >
              Edit
            </Button>
          )}

          {onTest && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onTest(agent.id)}
            >
              Test
            </Button>
          )}

          {agent.lifecycle_state === 'draft' && onActivate && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => onActivate(agent.id)}
            >
              Activate
            </Button>
          )}

          {agent.lifecycle_state === 'active' && onPause && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onPause(agent.id)}
            >
              Pause
            </Button>
          )}

          {onArchive && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onArchive(agent.id)}
              disabled={agent.lifecycle_state === 'archived'}
            >
              Archive
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export default AgentCard;
