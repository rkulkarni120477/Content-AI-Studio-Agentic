/**
 * ResultsPage
 * Tabbed interface for viewing run history, workflows, and analytics
 */

import React, { useState } from 'react';
import { useAppSelector } from '@app/hooks';
import { useAgents } from '@hooks/useAgents';
import { useWorkflows } from '@hooks/useWorkflows';
import { agentApi } from '@services/agentApi';
import { useEffect } from 'react';
import Loader from '@components/common/Loader/Loader';
import CostDisplay from '@components/AgentBuilder/CostDisplay';
import styles from './ResultsPage.module.scss';

type TabType = 'runs' | 'workflows' | 'analytics';

export function ResultsPage() {
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;
  const [activeTab, setActiveTab] = useState<TabType>('runs');

  const {
    agents,
    loading: agentsLoading,
  } = useAgents({ projectId, pageSize: 100 });

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
        <h1 className={styles.title}>Results & Analytics</h1>
        <p className={styles.subtitle}>
          View execution history and performance metrics
        </p>
      </div>

      <div className={styles.tabs}>
        <button
          className={`${styles.tab} ${activeTab === 'runs' ? styles['tab--active'] : ''}`}
          onClick={() => setActiveTab('runs')}
        >
          Run History
        </button>
        <button
          className={`${styles.tab} ${activeTab === 'workflows' ? styles['tab--active'] : ''}`}
          onClick={() => setActiveTab('workflows')}
        >
          Workflows
        </button>
        <button
          className={`${styles.tab} ${activeTab === 'analytics' ? styles['tab--active'] : ''}`}
          onClick={() => setActiveTab('analytics')}
        >
          Analytics
        </button>
      </div>

      <div className={styles.content}>
        {activeTab === 'runs' && (
          <div className={styles.tabContent}>
            <h2>Run History</h2>
            <p className={styles.placeholder}>
              Run history across all agents will be displayed here
            </p>
          </div>
        )}

        {activeTab === 'workflows' && (
          <div className={styles.tabContent}>
            <h2>Workflow Executions</h2>
            <p className={styles.placeholder}>
              Workflow execution history will be displayed here
            </p>
          </div>
        )}

        {activeTab === 'analytics' && (
          <div className={styles.tabContent}>
            <h2>Analytics Dashboard</h2>
            {usageLoading ? (
              <Loader size="lg" />
            ) : usage ? (
              <div className={styles.analytics}>
                <div className={styles.analyticsGrid}>
                  <div className={styles.metric}>
                    <div className={styles.metricLabel}>Total Runs</div>
                    <div className={styles.metricValue}>{usage.total_runs}</div>
                  </div>
                  <div className={styles.metric}>
                    <div className={styles.metricLabel}>Success Rate</div>
                    <div className={styles.metricValue}>
                      {usage.total_runs > 0
                        ? ((usage.completed_runs / usage.total_runs) * 100).toFixed(1)
                        : 0}
                      %
                    </div>
                  </div>
                  <div className={styles.metric}>
                    <div className={styles.metricLabel}>Total Cost</div>
                    <div className={styles.metricValue}>
                      ${usage.total_cost.toFixed(2)}
                    </div>
                  </div>
                  <div className={styles.metric}>
                    <div className={styles.metricLabel}>Total Tokens</div>
                    <div className={styles.metricValue}>
                      {usage.total_tokens.toLocaleString()}
                    </div>
                  </div>
                </div>

                {Object.keys(usage.cost_by_model || {}).length > 0 && (
                  <div className={styles.costBreakdown}>
                    <h3>Cost by Model</h3>
                    <table className={styles.costTable}>
                      <thead>
                        <tr>
                          <th>Model</th>
                          <th>Cost</th>
                          <th>Percentage</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(usage.cost_by_model).map(([model, cost]) => (
                          <tr key={model}>
                            <td>{model}</td>
                            <td>${(cost as number).toFixed(4)}</td>
                            <td>
                              {usage.total_cost > 0
                                ? (((cost as number) / usage.total_cost) * 100).toFixed(1)
                                : 0}
                              %
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

export default ResultsPage;
