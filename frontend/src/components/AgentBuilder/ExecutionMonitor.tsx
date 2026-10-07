/**
 * ExecutionMonitor Component
 * Shows real-time progress of agent/workflow execution
 */

import React, { useEffect, useState } from 'react';
import type { RunStatus } from '@types/agent';
import Loader from '@components/common/Loader/Loader';
import styles from './ExecutionMonitor.module.scss';

interface ExecutionMonitorProps {
  status: RunStatus;
  progress?: number; // 0-100
  currentStep?: string;
  elapsedTime?: number; // in milliseconds
  onCancel?: () => void;
  showCancelButton?: boolean;
  isPolling?: boolean;
}

const statusConfig: Record<RunStatus, { label: string; icon: string; color: string }> = {
  queued: { label: 'Queued', icon: '⏳', color: 'gray' },
  running: { label: 'Running', icon: '⚙️', color: 'blue' },
  completed: { label: 'Completed', icon: '✓', color: 'green' },
  failed: { label: 'Failed', icon: '✗', color: 'red' },
  cancelled: { label: 'Cancelled', icon: '⊘', color: 'orange' },
};

export function ExecutionMonitor({
  status,
  progress = 0,
  currentStep,
  elapsedTime = 0,
  onCancel,
  showCancelButton = true,
  isPolling = false,
}: ExecutionMonitorProps) {
  const [formattedTime, setFormattedTime] = useState('0s');
  const config = statusConfig[status] || statusConfig.queued;

  useEffect(() => {
    const minutes = Math.floor(elapsedTime / 60000);
    const seconds = Math.floor((elapsedTime % 60000) / 1000);

    if (minutes > 0) {
      setFormattedTime(`${minutes}m ${seconds}s`);
    } else {
      setFormattedTime(`${seconds}s`);
    }
  }, [elapsedTime]);

  const isActive = status === 'running' || status === 'queued';
  const isComplete = status === 'completed' || status === 'failed' || status === 'cancelled';

  return (
    <div className={`${styles.monitor} ${styles[`monitor--${config.color}`]}`}>
      <div className={styles.monitor__header}>
        <div className={styles.monitor__status}>
          <span className={styles.monitor__icon}>{config.icon}</span>
          <span className={styles.monitor__label}>{config.label}</span>
          {isActive && <Loader size="sm" color="inherit" />}
        </div>
        <div className={styles.monitor__time}>{formattedTime}</div>
      </div>

      {!isComplete && (
        <>
          <div className={styles.monitor__progress}>
            <div className={styles.monitor__progressBar}>
              <div
                className={styles.monitor__progressFill}
                style={{ width: `${Math.min(progress, 100)}%` }}
              />
            </div>
            <div className={styles.monitor__progressLabel}>
              {progress}% complete
            </div>
          </div>

          {currentStep && (
            <div className={styles.monitor__step}>
              <span className={styles.monitor__stepLabel}>Current step:</span>
              <span className={styles.monitor__stepName}>{currentStep}</span>
            </div>
          )}

          {showCancelButton && onCancel && (
            <button
              onClick={onCancel}
              className={styles.monitor__cancelBtn}
              disabled={status === 'queued'}
              title={status === 'queued' ? 'Cannot cancel queued executions' : 'Cancel execution'}
            >
              Cancel
            </button>
          )}
        </>
      )}

      {isPolling && (
        <div className={styles.monitor__polling}>
          <Loader size="xs" color="inherit" />
          <span>Polling for updates...</span>
        </div>
      )}
    </div>
  );
}

export default ExecutionMonitor;
