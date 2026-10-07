/**
 * WorkflowDiagram Component
 * Visual representation of workflow with agent steps
 */

import React from 'react';
import type { WorkflowDefinition, AgentStep } from '@types/workflow';
import styles from './WorkflowDiagram.module.scss';

interface WorkflowDiagramProps {
  definition: WorkflowDefinition;
  currentStepNumber?: number;
  onStepClick?: (stepNumber: number) => void;
  compact?: boolean;
  className?: string;
}

export function WorkflowDiagram({
  definition,
  currentStepNumber,
  onStepClick,
  compact = false,
  className,
}: WorkflowDiagramProps) {
  const steps = definition.steps || [];

  if (steps.length === 0) {
    return (
      <div className={`${styles.diagram} ${className || ''}`}>
        <div className={styles.diagram__empty}>
          No agents in this workflow yet
        </div>
      </div>
    );
  }

  return (
    <div className={`${styles.diagram} ${compact ? styles['diagram--compact'] : ''} ${className || ''}`}>
      <div className={styles.diagram__flow}>
        {steps.map((step, index) => (
          <React.Fragment key={index}>
            {index > 0 && (
              <div className={styles.diagram__arrow} aria-hidden="true">
                ↓
              </div>
            )}
            <div
              className={`${styles.diagram__step} ${
                currentStepNumber === step.step_number ? styles['diagram__step--active'] : ''
              }`}
              onClick={() => onStepClick?.(step.step_number)}
              role={onStepClick ? 'button' : undefined}
              tabIndex={onStepClick ? 0 : undefined}
              onKeyDown={(e) => {
                if (onStepClick && (e.key === 'Enter' || e.key === ' ')) {
                  onStepClick(step.step_number);
                }
              }}
            >
              <div className={styles.diagram__stepNumber}>
                Step {step.step_number}
              </div>
              <div className={styles.diagram__stepAgent}>
                {step.agent_name}
              </div>
              {step.timeout_seconds && (
                <div className={styles.diagram__stepTimeout}>
                  Timeout: {step.timeout_seconds}s
                </div>
              )}
            </div>
          </React.Fragment>
        ))}
      </div>

      {!compact && definition.variables && Object.keys(definition.variables).length > 0 && (
        <div className={styles.diagram__variables}>
          <h4 className={styles.diagram__variablesTitle}>Workflow Variables</h4>
          <div className={styles.diagram__variablesList}>
            {Object.entries(definition.variables).map(([key, value]) => (
              <div key={key} className={styles.diagram__variable}>
                <code>{key}</code>
                <span className={styles.diagram__variableType}>
                  {typeof value}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default WorkflowDiagram;
