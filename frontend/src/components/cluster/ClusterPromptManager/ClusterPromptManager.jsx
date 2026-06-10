import { useState } from 'react';
import toast from 'react-hot-toast';
import { useAppSelector } from '@app/hooks';
import { selectModelChoice } from '@features/dashboard/dashboardSlice';
import {
  CLUSTER_PROMPT_API_MESSAGE,
  CLUSTER_PROMPT_REQUIRED_ENDPOINTS,
  isClusterPromptApiAvailable,
} from '@features/clusterPrompt/clusterPromptApiDeps';
import BackendDependencyNotice from '@components/common/BackendDependencyNotice/BackendDependencyNotice';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import styles from './ClusterPromptManager.module.scss';

const NONE_CLUSTER = '';

function promptLabel(p) {
  const desc = p.description ? ` — ${p.description.slice(0, 40)}` : '';
  return `${p.name}${desc}`;
}

function notifyApiRequired() {
  toast.error(CLUSTER_PROMPT_API_MESSAGE);
}

export default function ClusterPromptManager({ clusters = [] }) {
  const modelChoice = useAppSelector(selectModelChoice) || 'GPT-5.4';
  const apiReady = isClusterPromptApiAvailable();
  const [tab, setTab] = useState(0);

  const [aiOpen, setAiOpen] = useState(false);
  const [aiMode, setAiMode] = useState('generate');
  const [aiContext, setAiContext] = useState('');
  const [draftSys, setDraftSys] = useState('');
  const [draftUsr, setDraftUsr] = useState('');

  const [assignClusterId, setAssignClusterId] = useState(NONE_CLUSTER);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [userPrompt, setUserPrompt] = useState('');

  const [viewClusterId, setViewClusterId] = useState('unassigned');
  const [viewPrompts] = useState([]);

  const clusterOptions = [
    { value: NONE_CLUSTER, label: '— None (unassigned) —' },
    ...clusters.map((c) => ({ value: String(c.id), label: c.name })),
  ];

  const viewOptions = [
    { value: 'unassigned', label: '— Unassigned —' },
    ...clusters.map((c) => ({ value: String(c.id), label: c.name })),
  ];

  function handleAiGenerate() {
    if (!apiReady) {
      notifyApiRequired();
      return;
    }
    if (!aiContext.trim()) {
      toast.error('Provide context or instructions before generating.');
    }
  }

  function handleCreate(e) {
    e.preventDefault();
    if (!apiReady) {
      notifyApiRequired();
      return;
    }
    if (!name.trim()) toast.error('Prompt name is required.');
  }

  function handleRemove() {
    if (!apiReady) notifyApiRequired();
  }

  return (
    <section className={styles.manager} aria-label="Cluster Prompt Manager">
      <div className={styles.manager__header}>🗂️ Cluster Prompt Manager</div>

      {!apiReady && (
        <div style={{ padding: '0 16px', paddingTop: 12 }}>
          <BackendDependencyNotice
            title="Backend dependency required"
            message={CLUSTER_PROMPT_API_MESSAGE}
            endpoints={CLUSTER_PROMPT_REQUIRED_ENDPOINTS}
          />
        </div>
      )}

      <div className={styles.tabs} role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 0}
          className={`${styles.tab} ${tab === 0 ? styles['tab--active'] : ''}`}
          onClick={() => setTab(0)}
        >
          ➕ Create
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 1}
          className={`${styles.tab} ${tab === 1 ? styles['tab--active'] : ''}`}
          onClick={() => setTab(1)}
        >
          📋 View / Manage
        </button>
      </div>

      {tab === 0 && (
        <div className={styles.panel} role="tabpanel">
          <div className={styles.aiExpander}>
            <button
              type="button"
              className={styles.aiExpander__toggle}
              onClick={() => setAiOpen((v) => !v)}
            >
              🤖 Generate or Refine with AI {aiOpen ? '▾' : '▸'}
            </button>
            {aiOpen && (
              <div className={styles.aiExpander__body}>
                <p className={styles.caption}>
                  Use AI to generate a fresh cluster prompt or to refine your draft.
                  Review the result below, then it will be pre-filled in the form.
                </p>
                <div className={styles.radioRow}>
                  <label>
                    <input
                      type="radio"
                      name="cp_ai_mode"
                      checked={aiMode === 'generate'}
                      onChange={() => setAiMode('generate')}
                    />
                    {' '}Generate Fresh Prompt
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="cp_ai_mode"
                      checked={aiMode === 'refine'}
                      onChange={() => setAiMode('refine')}
                    />
                    {' '}Refine My Draft
                  </label>
                </div>
                <label className={styles.textareaLabel}>
                  Context / Instructions
                  <textarea
                    rows={3}
                    className={styles.textarea}
                    value={aiContext}
                    onChange={(e) => setAiContext(e.target.value)}
                    placeholder="e.g. For clinical nursing courses. Focus on Bloom's levels 4–6."
                  />
                </label>
                {aiMode === 'refine' && (
                  <>
                    <p className={styles.caption}>Paste your drafts below for the AI to refine:</p>
                    <label className={styles.textareaLabel}>
                      Draft System Prompt
                      <textarea
                        rows={3}
                        className={styles.textarea}
                        value={draftSys}
                        onChange={(e) => setDraftSys(e.target.value)}
                      />
                    </label>
                    <label className={styles.textareaLabel}>
                      Draft User Prompt Template
                      <textarea
                        rows={2}
                        className={styles.textarea}
                        value={draftUsr}
                        onChange={(e) => setDraftUsr(e.target.value)}
                      />
                    </label>
                  </>
                )}
                <Button type="button" variant="primary" size="sm" fullWidth onClick={handleAiGenerate}>
                  ✨ Generate with AI
                </Button>
                <p className={styles.caption}>Model: {modelChoice}</p>
              </div>
            )}
          </div>

          <form className={styles.form} onSubmit={handleCreate}>
            <Select
              label="Assign to Cluster"
              hint="Optional. Prompt will be auto-injected into courses under the selected cluster."
              options={clusterOptions}
              value={assignClusterId}
              onChange={(e) => setAssignClusterId(e.target.value)}
            />
            <Input label="Prompt Name *" value={name} onChange={(e) => setName(e.target.value)} required />
            <Input
              label="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this prompt do?"
            />
            <label className={styles.textareaLabel}>
              System Prompt
              <textarea
                rows={5}
                className={styles.textarea}
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="You are an expert instructional designer…"
              />
            </label>
            <label className={styles.textareaLabel}>
              User Prompt Template
              <textarea
                rows={3}
                className={styles.textarea}
                value={userPrompt}
                onChange={(e) => setUserPrompt(e.target.value)}
                placeholder="Apply the following guidelines: {style_context}"
              />
            </label>
            <Button type="submit" variant="primary">
              💾 Create Cluster Prompt
            </Button>
          </form>
        </div>
      )}

      {tab === 1 && (
        <div className={styles.panel} role="tabpanel">
          <Select
            label="Select Cluster to View Prompts"
            options={viewOptions}
            value={viewClusterId}
            onChange={(e) => setViewClusterId(e.target.value)}
          />
          {viewPrompts.length === 0 ? (
            <p className={styles.empty}>
              {viewClusterId === 'unassigned'
                ? 'No unassigned cluster prompts yet.'
                : 'No cluster prompts assigned to this cluster yet.'}
            </p>
          ) : (
            <div className={styles.promptList}>
              {viewPrompts.map((p) => (
                <div key={p.id} className={styles.promptRow}>
                  <div className={styles.promptRow__info}>
                    <div className={styles.promptRow__name}>
                      {p.name}
                      {p.description && (
                        <span className={styles.promptRow__desc}> — {p.description}</span>
                      )}
                    </div>
                  </div>
                  <Button type="button" variant="ghost" size="sm" onClick={handleRemove}>
                    🗑️ Remove
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export { promptLabel };
