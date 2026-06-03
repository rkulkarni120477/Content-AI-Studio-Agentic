import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchPromptsThunk, commitPromptThunk, fetchPromptVersionsThunk, aiGeneratePromptThunk } from '@features/prompts/promptsThunks';
import { selectPrompts, selectPromptVersions, selectAiGenerated, selectPromptsLoading, selectAiGenerating } from '@features/prompts/promptsSlice';
import { commitPromptSchema } from '@utils/validation';
import { PROMPT_COMPONENT_TYPES } from '@utils/constants';
import { formatDate } from '@utils/helpers';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import Table from '@components/common/Table/Table';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import styles from './PromptsPage.module.scss';

const COMPONENT_OPTIONS = Object.entries(PROMPT_COMPONENT_TYPES).map(([, v]) => ({ value: v, label: v }));

const TEMPLATES = [
  { label: 'Lesson Generator', component_type: 'generate', system_prompt: 'You are an expert instructional designer.', user_prompt_template: 'Generate a lesson on: {{topic}} for {{audience}}.' },
  { label: 'Quiz Creator',     component_type: 'generate', system_prompt: 'You are a quiz design expert.', user_prompt_template: 'Create a 5-question quiz on: {{topic}}.' },
  { label: 'CDD Generator',    component_type: 'cdd',      system_prompt: 'You are an expert in course design.',  user_prompt_template: 'Create a CDD for course: {{course_title}}.' },
  { label: 'Blueprint Builder',component_type: 'blueprint',system_prompt: 'You design learning blueprints.',      user_prompt_template: 'Build a module blueprint for: {{module_title}}.' },
  { label: 'Style Analyzer',   component_type: 'style',    system_prompt: 'You analyze writing style.',          user_prompt_template: 'Analyze and summarize this style guide: {{style_content}}.' },
];

export default function PromptsPage() {
  const dispatch  = useAppDispatch();
  const prompts   = useAppSelector(selectPrompts);
  const versions  = useAppSelector(selectPromptVersions);
  const aiGen     = useAppSelector(selectAiGenerated);
  const isLoading = useAppSelector(selectPromptsLoading);
  const isAiGen   = useAppSelector(selectAiGenerating);

  const [selectedPrompt, setSelectedPrompt] = useState(null);
  const [showVersions, setShowVersions]     = useState(false);
  const [aiDescription, setAiDescription]  = useState('');

  const form = useForm({ resolver: zodResolver(commitPromptSchema) });

  useEffect(() => {
    dispatch(fetchPromptsThunk());
  }, [dispatch]);

  function applyTemplate(tpl) {
    form.reset({
      system_prompt:        tpl.system_prompt,
      user_prompt_template: tpl.user_prompt_template,
      component_type:       tpl.component_type,
    });
  }

  async function onCommit(data) {
    const result = await dispatch(commitPromptThunk(data));
    if (!result.error) form.reset();
  }

  async function onAiGenerate() {
    if (!aiDescription.trim()) return;
    dispatch(aiGeneratePromptThunk(aiDescription));
  }

  function applyAiGenerated() {
    if (!aiGen) return;
    form.setValue('system_prompt',        aiGen.system_prompt || '');
    form.setValue('user_prompt_template', aiGen.user_prompt_template || '');
  }

  const COLUMNS = [
    { key: 'name',           header: 'Name',      sortable: true },
    { key: 'component_type', header: 'Type' },
    { key: 'description',    header: 'Description', render: (v) => v || '—' },
    { key: 'active_version', header: 'Version',    render: (v) => v || '1' },
    {
      key: '__actions',
      header: '',
      render: (_, row) => (
        <div className={styles.rowActions}>
          <Button variant="ghost" size="xs" onClick={() => {
            setSelectedPrompt(row);
            dispatch(fetchPromptVersionsThunk(row.id));
            setShowVersions(true);
          }}>
            Versions
          </Button>
        </div>
      ),
    },
  ];

  return (
    <PageContainer title="Prompt Registry" breadcrumbs={[{ label: 'Prompts' }]}>
      <div className={styles.layout}>
        {/* Left: Commit form */}
        <section className={styles.panel}>
          <h2 className={styles.panel__title}>Commit Prompt Asset</h2>

          {/* Quick-start templates */}
          <div>
            <p className={styles.hint}>Quick start from template:</p>
            <div className={styles.templateBtns}>
              {TEMPLATES.map((t) => (
                <Button key={t.label} variant="ghost" size="xs" onClick={() => applyTemplate(t)}>
                  {t.label}
                </Button>
              ))}
            </div>
          </div>

          {/* AI generator */}
          <div className={styles.aiBox}>
            <p className={styles.aiBox__label}>Or generate with AI:</p>
            <textarea
              className={styles.aiBox__input}
              rows={2}
              placeholder="Describe what this prompt should do…"
              value={aiDescription}
              onChange={(e) => setAiDescription(e.target.value)}
            />
            <Button variant="secondary" size="sm" loading={isAiGen} onClick={onAiGenerate}>
              Generate Prompt
            </Button>
            {aiGen && (
              <div className={styles.aiGenResult}>
                <pre className={styles.aiGenResult__code}>{JSON.stringify(aiGen, null, 2)}</pre>
                <Button variant="ghost" size="sm" onClick={applyAiGenerated}>Use This</Button>
              </div>
            )}
          </div>

          <form onSubmit={form.handleSubmit(onCommit)} className={styles.form}>
            <Input label="Asset Name" required placeholder="e.g., lesson_generator" hint="lowercase, hyphens/underscores only" error={form.formState.errors.name?.message} {...form.register('name')} />
            <Select label="Component Type" required options={COMPONENT_OPTIONS} placeholder="Select type" error={form.formState.errors.component_type?.message} {...form.register('component_type')} />
            <Input label="Description" {...form.register('description')} />
            <div className={styles.form__field}>
              <label className={styles.form__label}>System Prompt <span className={styles.required}>*</span></label>
              <textarea className={`${styles.form__textarea} ${form.formState.errors.system_prompt ? styles['form__textarea--error'] : ''}`} rows={5} {...form.register('system_prompt')} />
              {form.formState.errors.system_prompt && <p className={styles.fieldError}>{form.formState.errors.system_prompt.message}</p>}
            </div>
            <div className={styles.form__field}>
              <label className={styles.form__label}>User Template <span className={styles.required}>*</span></label>
              <textarea className={`${styles.form__textarea} ${form.formState.errors.user_prompt_template ? styles['form__textarea--error'] : ''}`} rows={5} {...form.register('user_prompt_template')} />
              {form.formState.errors.user_prompt_template && <p className={styles.fieldError}>{form.formState.errors.user_prompt_template.message}</p>}
            </div>
            <Input label="Tags (comma-separated)" {...form.register('tags')} />
            <Button type="submit" variant="primary" fullWidth loading={isLoading}>
              Save Prompt Asset
            </Button>
          </form>
        </section>

        {/* Right: Prompt table */}
        <section className={styles.panel}>
          <h2 className={styles.panel__title}>Prompt Library</h2>
          <Table
            columns={COLUMNS}
            rows={prompts}
            rowKey="name"
            isLoading={isLoading}
            pagination
            pageSize={20}
            emptyTitle="No prompts yet"
            emptyMessage="Commit your first prompt asset."
          />
        </section>
      </div>

      {/* Version History Modal */}
      <Modal open={showVersions} onClose={() => setShowVersions(false)} title={`Versions — ${selectedPrompt?.name || ''}`} size="md">
        {versions.length === 0 ? (
          <p className={styles.emptyText}>No versions found.</p>
        ) : (
          <ul className={styles.versionList}>
            {versions.map((v) => (
              <li key={v.id} className={styles.versionItem}>
                <div>
                  <span className={styles.versionItem__ver}>v{v.version}</span>
                  {v.is_active && <span className={styles.badge__active}>Active</span>}
                  <span className={styles.versionItem__date}>{formatDate(v.created_at)}</span>
                  {v.change_reason && <p className={styles.versionItem__reason}>{v.change_reason}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Modal>
    </PageContainer>
  );
}
