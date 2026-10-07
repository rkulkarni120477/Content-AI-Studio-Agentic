/**
 * TestAgentPage
 * Test agent with sample input and monitor execution
 */

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { useAgentDetails, useAgentRuns } from '@hooks/useAgents';
import { agentApi } from '@services/agentApi';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import ExecutionMonitor from '@components/AgentBuilder/ExecutionMonitor';
import CostDisplay from '@components/AgentBuilder/CostDisplay';
import Loader from '@components/common/Loader/Loader';
import styles from './TestAgentPage.module.scss';

export function TestAgentPage() {
  const navigate = useNavigate();
  const { agentId } = useParams<{ agentId: string }>();
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;

  const agentIdNum = parseInt(agentId || '0', 10);
  const { agent, loading: agentLoading } = useAgentDetails({
    projectId,
    agentId: agentIdNum,
  });

  const [inputContent, setInputContent] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [currentRun, setCurrentRun] = useState<any>(null);
  const [pollInterval, setPollInterval] = useState<NodeJS.Timeout | null>(null);

  const handleRunAgent = async () => {
    if (!inputContent.trim()) {
      alert('Please enter some input content');
      return;
    }

    setIsRunning(true);
    try {
      const run = await agentApi.createAgentRun(projectId, agentIdNum, {
        input_content: inputContent,
      });
      setCurrentRun(run);

      // Poll for updates if not already completed
      if (run.status === 'running' || run.status === 'queued') {
        const interval = setInterval(async () => {
          try {
            const updated = await agentApi.getAgentRunDetails(projectId, agentIdNum, run.id);
            setCurrentRun(updated);
            if (updated.status === 'completed' || updated.status === 'failed') {
              clearInterval(interval);
            }
          } catch (err) {
            console.error('Failed to poll run status', err);
          }
        }, 2000);
        setPollInterval(interval);
      }
    } catch (err) {
      alert('Failed to run agent: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsRunning(false);
    }
  };

  useEffect(() => {
    return () => {
      if (pollInterval) {
        clearInterval(pollInterval);
      }
    };
  }, [pollInterval]);

  if (agentLoading || !agent) {
    return (
      <div className={styles.page}>
        <Loader size="lg" overlay />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <Button
          variant="secondary"
          onClick={() => navigate(-1)}
          className={styles.header__back}
        >
          ← Back
        </Button>
        <h1 className={styles.title}>Test Agent: {agent.name}</h1>
      </div>

      <div className={styles.container}>
        <div className={styles.testSection}>
          <div className={styles.form}>
            <h2 className={styles.formTitle}>Agent Input</h2>
            <textarea
              value={inputContent}
              onChange={(e) => setInputContent(e.target.value)}
              placeholder="Enter input content to test the agent..."
              className={styles.formTextarea}
              disabled={isRunning}
              rows={8}
            />
            <Button
              variant="primary"
              size="lg"
              onClick={handleRunAgent}
              loading={isRunning}
              fullWidth
              className={styles.formSubmit}
            >
              Run Agent
            </Button>
          </div>

          {currentRun && (
            <div className={styles.results}>
              <h2 className={styles.resultsTitle}>Execution Results</h2>

              <ExecutionMonitor
                status={currentRun.status}
                progress={currentRun.status === 'completed' ? 100 : 50}
                elapsedTime={
                  currentRun.completed_at
                    ? new Date(currentRun.completed_at).getTime() - new Date(currentRun.queued_at).getTime()
                    : Date.now() - new Date(currentRun.queued_at).getTime()
                }
              />

              {currentRun.output_content && (
                <div className={styles.output}>
                  <h3 className={styles.outputTitle}>Output</h3>
                  <div className={styles.outputContent}>
                    {currentRun.output_content}
                  </div>
                </div>
              )}

              {currentRun.error_message && (
                <div className={styles.error}>
                  <h3 className={styles.errorTitle}>Error</h3>
                  <p className={styles.errorMessage}>
                    {currentRun.error_message}
                  </p>
                </div>
              )}

              <CostDisplay
                tokenUsage={currentRun.token_usage}
                cost={currentRun.cost}
              />
            </div>
          )}
        </div>

        <div className={styles.sidebar}>
          <div className={styles.agentInfo}>
            <h3 className={styles.agentInfoTitle}>Agent Info</h3>
            <div className={styles.agentInfoItem}>
              <span className={styles.agentInfoLabel}>Model:</span>
              <span className={styles.agentInfoValue}>
                {agent.configuration?.model_id || 'Default'}
              </span>
            </div>
            <div className={styles.agentInfoItem}>
              <span className={styles.agentInfoLabel}>Temperature:</span>
              <span className={styles.agentInfoValue}>
                {agent.configuration?.temperature || 0.7}
              </span>
            </div>
            <div className={styles.agentInfoItem}>
              <span className={styles.agentInfoLabel}>Max Tokens:</span>
              <span className={styles.agentInfoValue}>
                {agent.configuration?.max_tokens || 2000}
              </span>
            </div>
            <div className={styles.agentInfoItem}>
              <span className={styles.agentInfoLabel}>Status:</span>
              <span className={`${styles.agentInfoValue} ${styles[`status--${agent.lifecycle_state}`]}`}>
                {agent.lifecycle_state}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default TestAgentPage;
