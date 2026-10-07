/**
 * CostDisplay Component
 * Shows cost information for a run or workflow execution
 */

import React from 'react';
import type { TokenUsage, CostInfo } from '@types/agent';
import styles from './CostDisplay.module.scss';

interface CostDisplayProps {
  tokenUsage?: TokenUsage;
  cost?: CostInfo;
  compact?: boolean;
  className?: string;
}

export function CostDisplay({
  tokenUsage,
  cost,
  compact = false,
  className,
}: CostDisplayProps) {
  if (!tokenUsage && !cost) {
    return null;
  }

  if (compact) {
    return (
      <div className={`${styles.cost} ${styles['cost--compact']} ${className || ''}`}>
        <span className={styles.cost__label}>Cost:</span>
        {cost && (
          <span className={styles.cost__amount}>
            {cost.currency === 'USD' ? '$' : ''}
            {cost.total_cost.toFixed(4)}
          </span>
        )}
        {tokenUsage && (
          <span className={styles.cost__tokens}>
            {tokenUsage.total_tokens.toLocaleString()} tokens
          </span>
        )}
      </div>
    );
  }

  return (
    <div className={`${styles.cost} ${className || ''}`}>
      {tokenUsage && (
        <div className={styles.cost__section}>
          <h4 className={styles.cost__title}>Token Usage</h4>
          <div className={styles.cost__grid}>
            <div className={styles.cost__item}>
              <span className={styles.cost__label}>Input:</span>
              <span className={styles.cost__value}>
                {tokenUsage.input_tokens.toLocaleString()}
              </span>
            </div>
            <div className={styles.cost__item}>
              <span className={styles.cost__label}>Output:</span>
              <span className={styles.cost__value}>
                {tokenUsage.output_tokens.toLocaleString()}
              </span>
            </div>
            <div className={styles.cost__item}>
              <span className={styles.cost__label}>Total:</span>
              <span className={`${styles.cost__value} ${styles['cost__value--bold']}`}>
                {tokenUsage.total_tokens.toLocaleString()}
              </span>
            </div>
          </div>
        </div>
      )}

      {cost && (
        <div className={styles.cost__section}>
          <h4 className={styles.cost__title}>Cost Breakdown</h4>
          <div className={styles.cost__grid}>
            <div className={styles.cost__item}>
              <span className={styles.cost__label}>Model:</span>
              <span className={styles.cost__value}>{cost.model_name}</span>
            </div>
            <div className={styles.cost__item}>
              <span className={styles.cost__label}>Input Cost:</span>
              <span className={styles.cost__value}>
                {cost.currency === 'USD' ? '$' : ''}
                {cost.input_cost.toFixed(6)}
              </span>
            </div>
            <div className={styles.cost__item}>
              <span className={styles.cost__label}>Output Cost:</span>
              <span className={styles.cost__value}>
                {cost.currency === 'USD' ? '$' : ''}
                {cost.output_cost.toFixed(6)}
              </span>
            </div>
            <div className={styles.cost__item}>
              <span className={styles.cost__label}>Total Cost:</span>
              <span className={`${styles.cost__value} ${styles['cost__value--bold']}`}>
                {cost.currency === 'USD' ? '$' : ''}
                {cost.total_cost.toFixed(4)}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default CostDisplay;
