/**
 * BudgetPage
 * Display project budget and spending analytics
 */

import React from 'react';
import { useAppSelector } from '@app/hooks';
import { useAgentBudget } from '@hooks/useAgents';
import { agentApi } from '@services/agentApi';
import { useEffect, useState } from 'react';
import BudgetBar from '@components/AgentBuilder/BudgetBar';
import Loader from '@components/common/Loader/Loader';
import RoleGate from '@components/AgentBuilder/RoleGate';
import styles from './BudgetPage.module.scss';

export function BudgetPage() {
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;

  const { budget, loading: budgetLoading } = useAgentBudget({ projectId });
  const [usage, setUsage] = useState<any>(null);
  const [usageLoading, setUsageLoading] = useState(false);

  useEffect(() => {
    const fetchUsage = async () => {
      setUsageLoading(true);
      try {
        const data = await agentApi.getUsageSummary(projectId);
        setUsage(data);
      } catch (err) {
        console.error('Failed to fetch usage', err);
      } finally {
        setUsageLoading(false);
      }
    };

    if (projectId) {
      fetchUsage();
    }
  }, [projectId]);

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>Budget & Costs</h1>
        <p className={styles.subtitle}>
          Monitor spending and budget usage
        </p>
      </div>

      {budgetLoading ? (
        <Loader size="lg" overlay />
      ) : (
        <>
          <div className={styles.budgetSection}>
            <BudgetBar budget={budget} />
          </div>

          {usageLoading ? (
            <Loader size="lg" />
          ) : usage ? (
            <div className={styles.usageSection}>
              <h2 className={styles.usageTitle}>Usage Summary</h2>

              <div className={styles.statsGrid}>
                <div className={styles.stat}>
                  <div className={styles.stat__value}>
                    {usage.total_runs}
                  </div>
                  <div className={styles.stat__label}>Total Runs</div>
                </div>

                <div className={styles.stat}>
                  <div className={styles.stat__value}>
                    {usage.completed_runs}
                  </div>
                  <div className={styles.stat__label}>Completed</div>
                </div>

                <div className={styles.stat}>
                  <div className={styles.stat__value}>
                    {usage.failed_runs}
                  </div>
                  <div className={styles.stat__label}>Failed</div>
                </div>

                <div className={styles.stat}>
                  <div className={styles.stat__value}>
                    {usage.total_tokens.toLocaleString()}
                  </div>
                  <div className={styles.stat__label}>Total Tokens</div>
                </div>

                <div className={styles.stat}>
                  <div className={styles.stat__value}>
                    ${usage.total_cost.toFixed(2)}
                  </div>
                  <div className={styles.stat__label}>Total Cost</div>
                </div>
              </div>

              {Object.keys(usage.cost_by_model || {}).length > 0 && (
                <div className={styles.costByModel}>
                  <h3 className={styles.costByModel__title}>Cost by Model</h3>
                  <table className={styles.costByModel__table}>
                    <thead>
                      <tr>
                        <th>Model</th>
                        <th>Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(usage.cost_by_model).map(([model, cost]) => (
                        <tr key={model}>
                          <td>{model}</td>
                          <td>${(cost as number).toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : null}

          <RoleGate requiredRole="admin">
            <div className={styles.adminSection}>
              <h2 className={styles.adminTitle}>Admin Controls</h2>
              <p className={styles.adminText}>
                As a project admin, you can manage budget limits and spending policies.
              </p>
              <button className={styles.adminBtn} disabled>
                Edit Budget Limit (Coming Soon)
              </button>
            </div>
          </RoleGate>
        </>
      )}
    </div>
  );
}

export default BudgetPage;
