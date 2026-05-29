import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  selectWorkspaceConfig, selectSelectedCourse, selectModels,
  setModelChoice, setExpertDomain, setTargetAudience, setAudienceCategory,
} from '@features/dashboard/dashboardSlice';
import { fetchModelsThunk, saveWorkspaceConfigThunk } from '@features/dashboard/dashboardThunks';
import toast from 'react-hot-toast';
import Button from '@components/common/Button/Button';
import styles from './TargetModelPanel.module.scss';

const AUDIENCE_OPTIONS = [
  'Professional/Corporate',
  'Undergrad/Graduate',
  'K-12 Student',
  'General',
];

export default function TargetModelPanel() {
  const dispatch = useAppDispatch();
  const config = useAppSelector(selectWorkspaceConfig);
  const models = useAppSelector(selectModels);
  const course = useAppSelector(selectSelectedCourse);
  const [open, setOpen] = useState(true);
  const [form, setForm] = useState({
    model: config.modelChoice,
    domain: config.expertDomain,
    audience: config.targetAudience,
    audCat: config.audienceCategory || 'Professional/Corporate',
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => { dispatch(fetchModelsThunk()); }, [dispatch]);

  useEffect(() => {
    setForm({
      model: config.modelChoice,
      domain: config.expertDomain,
      audience: config.targetAudience,
      audCat: config.audienceCategory || 'Professional/Corporate',
    });
  }, [config]);

  const modelOptions = models?.items?.length
    ? models.items.map((m) => m.display_name || m.name)
    : [config.modelChoice];

  async function handleApply(e) {
    e.preventDefault();
    setSaving(true);
    dispatch(setModelChoice(form.model));
    dispatch(setExpertDomain(form.domain));
    dispatch(setTargetAudience(form.audience));
    dispatch(setAudienceCategory(form.audCat));
    if (course?.id) {
      try {
        await dispatch(saveWorkspaceConfigThunk({
          courseId: course.id,
          config: {
            model_choice: form.model,
            expert_domain: form.domain,
            target_audience: form.audience,
            audience_category: form.audCat,
          },
        })).unwrap();
        toast.success('Configuration applied');
      } catch {
        toast.error('Failed to save configuration');
      }
    }
    setSaving(false);
  }

  return (
    <div className={styles.panel}>
      <button type="button" className={styles.panel__toggle} onClick={() => setOpen((v) => !v)}>
        🎯 Target &amp; Model {open ? '▾' : '▸'}
      </button>
      {open && (
        <form className={styles.form} onSubmit={handleApply}>
          <p className={styles.caption}>Configure the AI model and audience profile. Applied to all generations.</p>
          <label className={styles.field}>
            LLM Model
            <select value={form.model} onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))} className={styles.select}>
              {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className={styles.field}>
            Expert Domain
            <input className={styles.input} value={form.domain} placeholder="e.g. Nursing, Software Dev" onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))} />
          </label>
          <label className={styles.field}>
            Audience Category
            <select value={form.audCat} onChange={(e) => setForm((f) => ({ ...f, audCat: e.target.value }))} className={styles.select}>
              {AUDIENCE_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <label className={styles.field}>
            Specific Audience Detail
            <input className={styles.input} value={form.audience} placeholder="e.g. Grade 7 students" onChange={(e) => setForm((f) => ({ ...f, audience: e.target.value }))} />
          </label>
          <Button type="submit" variant="primary" size="sm" fullWidth loading={saving}>
            ✅ Apply Configuration
          </Button>
        </form>
      )}
    </div>
  );
}
