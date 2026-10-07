/**
 * ExecuteWorkflowPage
 * Execute workflow and monitor execution
 */

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { useWorkflowDetails, useWorkflowRuns } from '@hooks/useWorkflows';
import { workflowApi } from '@services/workflowApi';
import Button from '@components/common/Button/Button';
import ExecutionMonitor from '@components/AgentBuilder/ExecutionMonitor';
import WorkflowDiagram from '@components/AgentBuilder/WorkflowDiagram';
import CostDisplay from '@components/AgentBuilder/CostDisplay';
import Loader from '@components/common/Loader/Loader';
import styles from './ExecuteWorkflowPage.module.scss';

export function ExecuteWorkflowPage() {
  const navigate = useNavigate();
  const { workflowId } = useParams<{ workflowId: string }>();
  const { project } = useAppSelector((state) => state.dashboard);
  const projectId = project?.id || 0;

  const workflowIdNum = parseInt(workflowId || '0', 10);
  const { workflow, loading: workflowLoading } = useWorkflowDetails({
    projectId,
    workflowId: workflowIdNum,
  });

  const [inputContent, setInputContent] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [currentRun, setCurrentRun] = useState<any>(null);
  const [pollInterval, setPollInterval] = useState<NodeJS.Timeout | null>(null);

  const handleExecuteWorkflow = async () => {
    if (!inputContent.trim()) {
      alert('Please enter initial input');
      return;
    }

    setIsExecuting(true);
    try {
      const run = await workflowApi.executeWorkflow(projectId, workflowIdNum, {
        initial_input: inputContent,
      });
      setCurrentRun(run);

      // Poll for updates
      if (run.status === 'running' || run.status === 'queued') {
        const interval = setInterval(async () => {
          try {
            const updated = await workflowApi.getWorkflowRun(projectId, workflowIdNum, run.id);
            setCurrentRun(updated);
            if (updated.status === 'completed' || updated.status === 'failed') {
              clearInterval(interval);
            }
          } catch (err) {
            console.error('Failed to poll workflow status', err);
          }
        }, 2000);
        setPollInterval(interval);
      }
    } catch (err) {
      alert('Failed to execute workflow: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsExecuting(false);
    }
  };

  useEffect(() => {
    return () => {
      if (pollInterval) {
        clearInterval(pollInterval);
      }
    };
  }, [pollInterval]);

  if (workflowLoading || !workflow) {
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
        <h1 className={styles.title}>Execute: {workflow.name}</h1>
      </div>

      <div className={styles.container}>
        <div className={styles.main}>
          <div className={styles.form}>
            <h2 className={styles.form__title}>Workflow Input</h2>
            <textarea
              value={inputContent}
              onChange={(e) => setInputContent(e.target.value)}
              placeholder="Enter initial input for the workflow..."
              className={styles.form__textarea}
              disabled={isExecuting}
              rows={8}
            />
            <Button
              variant="primary"
              size="lg"
              onClick={handleExecuteWorkflow}
              loading={isExecuting}
              fullWidth
              className={styles.form__submit}
            >
              Execute Workflow
            </Button>
          </div>

          {currentRun && (
            <div className={styles.results}>
              <h2 className={styles.results__title}>Execution Status</h2>

              <ExecutionMonitor
                status={currentRun.status}
                progress={
                  currentRun.total_steps > 0
                    ? (currentRun.current_step_number / currentRun.total_steps) * 100
                    : 0
                }
                currentStep={`Step ${currentRun.current_step_number} of ${currentRun.total_steps}`}
                elapsedTime={
                  currentRun.completed_at
                    ? new Date(currentRun.completed_at).getTime() - new Date(currentRun.queued_at).getTime()
                    : Date.now() - new Date(currentRun.queued_at).getTime()
                }
              />

              {currentRun.steps && currentRun.steps.length > 0 && (
                <div className={styles.steps}>
                  <h3 className={styles.steps__title}>Execution Steps</h3>
                  <div className={styles.steps__list}>
                    {currentRun.steps.map((step: any) => (
                      <div key={step.step_number} className={styles.step}>
                        <div className={styles.step__header}>
                          <span className={styles.step__number}>
                            Step {step.step_number}
                          </span>
                          <span className={styles.step__agent}>
                            {step.agent_name}
                          </span>
                          <span className={`${styles.step__status} ${styles[`step__status--${step.status}`]}`}>
                            {step.status}
                          </span>
                        </div>
                        {step.output_data && (
                          <div className={styles.step__output}>
                            <strong>Output:</strong>
                            <pre>{JSON.stringify(step.output_data, null, 2)}</pre>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {currentRun.final_output && (
                <div className={styles.output}>
                  <h3 className={styles.output__title}>Final Output</h3>
                  <div className={styles.output__content}>
                    {currentRun.final_output}
                  </div>
                </div>
              )}

              {currentRun.error_message && (
                <div className={styles.error}>
                  <h3 className={styles.error__title}>Error</h3>
                  <p className={styles.error__message}>{currentRun.error_message}</p>
                </div>
              )}

              <CostDisplay
                tokenUsage={currentRun.total_token_usage}
                cost={currentRun.total_cost}
              />
            </div>
          )}
        </div>

        <div className={styles.sidebar}>
          <div className={styles.diagram}>
            <h3 className={styles.diagram__title}>Workflow Structure</h3>
            <WorkflowDiagram
              definition={workflow.definition}
              currentStepNumber={currentRun?.current_step_number}
              compact
            />
          </div>

          <div className={styles.info}>
            <h3 className={styles.info__title}>Workflow Info</h3>
            <div className={styles.info__item}>
              <span className={styles.info__label}>Owner:</span>
              <span className={styles.info__value}>{workflow.owner}</span>
            </div>
            <div className={styles.info__item}>
              <span className={styles.info__label}>Agents:</span>
              <span className={styles.info__value}>
                {workflow.definition.steps?.length || 0}
              </span>
            </div>
            <div className={styles.info__item}>
              <span className={styles.info__label}>Created:</span>
              <span className={styles.info__value}>
                {new Date(workflow.created_at).toLocaleDateString()}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ExecuteWorkflowPage;
