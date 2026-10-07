/**
 * BudgetBar Component
 * Shows current spend vs budget limit with progress visualization
 */

import React from 'react';
import type { BudgetStatus } from '@types/agent';
import styles from './BudgetBar.module.scss';

interface BudgetBarProps {
  budget?: BudgetStatus;
  loading?: boolean;
  className?: string;
}

export function BudgetBar({ budget, loading = false, className }: BudgetBarProps) {
  if (loading || !budget) {
    return (
      <div className={`${styles.budget} ${styles['budget--loading']} ${className || ''}`}>
        <div className={styles.budget__placeholder}>Loading budget info...</div>
      </div>
    );
  }

  const percentage = budget.percentage_used;
  let statusColor = 'green';

  if (percentage > 90) {
    statusColor = 'red';
  } else if (percentage > 70) {
    statusColor = 'yellow';
  }

  const isExceeded = budget.is_exceeded;
  const warningMessage = isExceeded
    ? 'Budget limit exceeded! No new runs can be started.'
    : percentage > 85
      ? 'Approaching budget limit. Use with caution.'
      : undefined;

  return (
    <div className={`${styles.budget} ${className || ''}`}>
      <div className={styles.budget__header}>
        <h3 className={styles.budget__title}>Project Budget</h3>
        <div className={styles.budget__amounts}>
          <span className={styles.budget__current}>
            ${budget.current_spend.toFixed(2)}
          </span>
          <span className={styles.budget__separator}>/</span>
          <span className={styles.budget__limit}>
            ${budget.total_budget.toFixed(2)}
          </span>
        </div>
      </div>

      <div className={styles.budget__bar}>
        <div
          className={`${styles.budget__fill} ${styles[`budget__fill--${statusColor}`]}`}
          style={{ width: `${Math.min(percentage, 100)}%` }}
          role="progressbar"
          aria-valuenow={Math.round(percentage)}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>

      <div className={styles.budget__info}>
        <div className={styles.budget__stat}>
          <span className={styles.budget__label}>Used:</span>
          <span className={styles.budget__value}>{percentage.toFixed(1)}%</span>
        </div>
        <div className={styles.budget__stat}>
          <span className={styles.budget__label}>Remaining:</span>
          <span className={styles.budget__value}>
            ${budget.remaining_budget.toFixed(2)}
          </span>
        </div>
        <div className={styles.budget__stat}>
          <span className={styles.budget__label}>Period:</span>
          <span className={styles.budget__value}>
            {new Date(budget.period_start).toLocaleDateString()} -
            {' '}
            {new Date(budget.period_end).toLocaleDateString()}
          </span>
        </div>
      </div>

      {warningMessage && (
        <div className={`${styles.budget__warning} ${styles[`budget__warning--${statusColor}`]}`}>
          {warningMessage}
        </div>
      )}
    </div>
  );
}

export default BudgetBar;
