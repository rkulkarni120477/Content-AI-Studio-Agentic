import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  selectModelChoice, selectExpertDomain, selectTargetAudience, selectAudienceCategory,
  selectModels,
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
  const modelChoice = useAppSelector(selectModelChoice);
  const expertDomain = useAppSelector(selectExpertDomain);
  const targetAudience = useAppSelector(selectTargetAudience);
  const audienceCategory = useAppSelector(selectAudienceCategory);
  const models = useAppSelector(selectModels);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    model: modelChoice,
    domain: expertDomain,
    audience: targetAudience,
    audCat: audienceCategory || 'Professional/Corporate',
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => { dispatch(fetchModelsThunk()); }, [dispatch]);

  // Sync each field from Redux only when that value actually changes.
  // selectWorkspaceConfig used to return a new object every time, so any
  // unrelated store update re-ran the effect and snapped the dropdown back.
  useEffect(() => {
    setForm((f) => (f.model === modelChoice ? f : { ...f, model: modelChoice }));
  }, [modelChoice]);
  useEffect(() => {
    setForm((f) => (f.domain === expertDomain ? f : { ...f, domain: expertDomain }));
  }, [expertDomain]);
  useEffect(() => {
    setForm((f) => (f.audience === targetAudience ? f : { ...f, audience: targetAudience }));
  }, [targetAudience]);
  useEffect(() => {
    const next = audienceCategory || 'Professional/Corporate';
    setForm((f) => (f.audCat === next ? f : { ...f, audCat: next }));
  }, [audienceCategory]);

  const openAiModels = (models?.items || []).filter((m) => (m.provider || '').toLowerCase() === 'openai');
  const bedrockModels = (models?.items || []).filter((m) => (m.provider || '').toLowerCase() === 'bedrock');
  const knownLabels = new Set(
    (models?.items || []).map((m) => m.display_name || m.name).filter(Boolean),
  );
  const fallbackOptions = [form.model, modelChoice].filter((m, i, arr) => m && arr.indexOf(m) === i);

  function handleModelChange(e) {
    const model = e.target.value;
    setForm((f) => ({ ...f, model }));
    // Stick for the rest of the session immediately — Apply only persists to JWT.
    dispatch(setModelChoice(model));
  }

  async function handleApply(e) {
    e.preventDefault();
    setSaving(true);
    dispatch(setModelChoice(form.model));
    dispatch(setExpertDomain(form.domain));
    dispatch(setTargetAudience(form.audience));
    dispatch(setAudienceCategory(form.audCat));
    try {
      await dispatch(saveWorkspaceConfigThunk({
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
            <select value={form.model} onChange={handleModelChange} className={styles.select}>
              {models?.items?.length ? (
                <>
                  {openAiModels.length > 0 && (
                    <optgroup label="OpenAI GPT Models">
                      {openAiModels.map((m) => {
                        const label = m.display_name || m.name;
                        return <option key={label} value={label}>{label}</option>;
                      })}
                    </optgroup>
                  )}
                  {bedrockModels.length > 0 && (
                    <optgroup label="AWS Bedrock Models">
                      {bedrockModels.map((m) => {
                        const label = m.display_name || m.name;
                        return <option key={label} value={label}>{label}</option>;
                      })}
                    </optgroup>
                  )}
                  {/* Keep the current choice visible if it is not in the catalog yet. */}
                  {form.model && !knownLabels.has(form.model) && (
                    <option value={form.model}>{form.model}</option>
                  )}
                </>
              ) : (
                fallbackOptions.map((m) => <option key={m} value={m}>{m}</option>)
              )}
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
