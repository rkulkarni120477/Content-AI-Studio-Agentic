import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchPromptsThunk } from '@features/prompts/promptsThunks';
import { promptsService } from '@features/prompts/services/promptsService';
import { selectPrompts, selectPromptsLoading } from '@features/prompts/promptsSlice';
import { selectModelChoice } from '@features/dashboard/dashboardSlice';
import {
  COMPONENT_LABELS,
  promptDisplayLabel,
  buildPromptDownloadMd,
} from '@utils/promptDefaults';
import { formatDate, downloadBlob } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import Loader from '@components/common/Loader/Loader';
import toast from 'react-hot-toast';
import { extractErrorMessage } from '@utils/helpers';
import styles from './PromptLibraryPanel.module.scss';

const PANEL_TABS = [
  { id: 'view', label: '📄 View' },
  { id: 'edit', label: '✏️ Edit & Save' },
  { id: 'history', label: '📋 Version History' },
  { id: 'ai', label: '🤖 Improve with AI' },
];

function formatMetaDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

export default function PromptLibraryPanel({
  component = 'style',
  showHeader = true,
  defaultSettingsOpen = true,
}) {
  const dispatch = useAppDispatch();
  const prompts = useAppSelector(selectPrompts);
  const promptsLoading = useAppSelector(selectPromptsLoading);
  const modelChoice = useAppSelector(selectModelChoice);

  const [settingsOpen, setSettingsOpen] = useState(defaultSettingsOpen);
  const [activeTab, setActiveTab] = useState('view');
  const [selectedKey, setSelectedKey] = useState('');
  const [detail, setDetail] = useState(null);
  const [versions, setVersions] = useState([]);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [saving, setSaving] = useState(false);
  const [expandedVersionId, setExpandedVersionId] = useState(null);

  const [editForm, setEditForm] = useState({ system: '', user: '', version: 'v2', reason: '' });
  const [createForm, setCreateForm] = useState({
    name: '', description: '', tags: component, system: '', user: '',
  });
  const [aiInstr, setAiInstr] = useState('');
  const [aiSuggestion, setAiSuggestion] = useState(null);
  const [improving, setImproving] = useState(false);

  const compLabel = COMPONENT_LABELS[component] || component;

  const filteredPrompts = useMemo(
    () => (prompts || []).filter(
      (p) => !p.component_type || p.component_type === component,
    ),
    [prompts, component],
  );

  const labelToPrompt = useMemo(() => {
    const map = new Map();
    filteredPrompts.forEach((p) => {
      let lbl = promptDisplayLabel(p);
      if (map.has(lbl)) lbl = `${lbl} (${p.id})`;
      map.set(lbl, p);
    });
    return map;
  }, [filteredPrompts]);

  const promptOptions = useMemo(
    () => Array.from(labelToPrompt.keys()).map((l) => ({ value: l, label: l })),
    [labelToPrompt],
  );

  const selectedPrompt = selectedKey ? labelToPrompt.get(selectedKey) : null;

  const loadDetail = useCallback(async (promptId) => {
    if (!promptId) {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    try {
      const d = await promptsService.getPromptDetail(promptId);
      setDetail(d);
      setCreateForm((f) => ({
        ...f,
        system: d.system_prompt || '',
        user: d.user_prompt_template || '',
      }));
    } catch (e) {
      toast.error(extractErrorMessage(e));
      setDetail(null);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    if (!detail) return;
    setEditForm((f) => ({
      ...f,
      system: detail.system_prompt || '',
      user: detail.user_prompt_template || '',
    }));
  }, [detail?.id, detail?.system_prompt, detail?.user_prompt_template]);

  useEffect(() => {
    if (versions.length > 0) {
      setEditForm((f) => ({ ...f, version: `v${versions.length + 1}` }));
    }
  }, [versions.length, selectedPrompt?.id]);

  const loadVersions = useCallback(async (promptId) => {
    if (!promptId) {
      setVersions([]);
      return;
    }
    setLoadingVersions(true);
    try {
      const list = await promptsService.getVersions(promptId);
      const sorted = [...(list || [])].sort(
        (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0),
      );
      setVersions(sorted);
      const active = sorted.find((v) => v.is_active);
      setExpandedVersionId(active?.id ?? sorted[0]?.id ?? null);
      setEditForm((f) => ({
        ...f,
        version: `v${(list?.length || 0) + 1}`,
      }));
    } catch (e) {
      toast.error(extractErrorMessage(e));
      setVersions([]);
    } finally {
      setLoadingVersions(false);
    }
  }, []);

  useEffect(() => {
    dispatch(fetchPromptsThunk({ component }));
  }, [dispatch, component]);

  useEffect(() => {
    if (promptsLoading || promptOptions.length === 0) {
      if (!promptsLoading && promptOptions.length === 0) {
        setSelectedKey('');
        setDetail(null);
      }
      return;
    }
    if (selectedKey && labelToPrompt.has(selectedKey)) return;

    const def = filteredPrompts.find((p) => p.is_default) || filteredPrompts[0];
    const lbl = promptDisplayLabel(def);
    const key = labelToPrompt.has(lbl) ? lbl : promptOptions[0].value;
    setSelectedKey(key);
    const p = labelToPrompt.get(key);
    if (p?.id) {
      loadDetail(p.id);
      loadVersions(p.id);
    }
  }, [promptsLoading, promptOptions.length, filteredPrompts, selectedKey, labelToPrompt, loadDetail, loadVersions]);

  useEffect(() => {
    if (activeTab === 'history' && selectedPrompt?.id) {
      loadVersions(selectedPrompt.id);
    }
  }, [activeTab, selectedPrompt?.id, loadVersions]);

  function onSelectPrompt(label) {
    setSelectedKey(label);
    setAiSuggestion(null);
    const p = labelToPrompt.get(label);
    if (p?.id) {
      loadDetail(p.id);
      loadVersions(p.id);
    }
  }

  async function refreshAfterSave(promptId) {
    await dispatch(fetchPromptsThunk({ component }));
    await loadDetail(promptId);
    await loadVersions(promptId);
  }

  async function handleSaveVersion() {
    if (!selectedPrompt?.id) return;
    if (!editForm.version.trim()) {
      toast.error('Version tag is required.');
      return;
    }
    setSaving(true);
    try {
      await promptsService.commitVersion(selectedPrompt.id, {
        version: editForm.version.trim(),
        system_prompt: editForm.system,
        user_prompt_template: editForm.user,
        change_reason: editForm.reason || 'Manual edit via Prompt Library.',
      });
      toast.success(`Version ${editForm.version} saved.`);
      await refreshAfterSave(selectedPrompt.id);
      setActiveTab('history');
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleCreateAsset() {
    if (!createForm.name.trim()) {
      toast.error('Asset name is required.');
      return;
    }
    setSaving(true);
    try {
      const created = await promptsService.commitPrompt({
        name: createForm.name.trim(),
        description: createForm.description,
        tags: createForm.tags || component,
        component_type: component,
        system_prompt: createForm.system,
        user_prompt_template: createForm.user,
        change_reason: 'Created from Prompt Library panel.',
      });
      toast.success(`"${created.name}" created and activated.`);
      await dispatch(fetchPromptsThunk({ component }));
      const lbl = promptDisplayLabel(created);
      setSelectedKey(labelToPrompt.has(lbl) ? lbl : created.name);
      setDetail(created);
      await loadVersions(created.id);
      setCreateForm({ name: '', description: '', tags: component, system: '', user: '' });
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleRestoreVersion(versionRow) {
    if (!selectedPrompt?.id || !versionRow) return;
    setSaving(true);
    try {
      const tag = `${versionRow.version}-restore`;
      await promptsService.commitVersion(selectedPrompt.id, {
        version: tag,
        system_prompt: versionRow.system_prompt || '',
        user_prompt_template: versionRow.user_prompt_template || '',
        change_reason: `Restored from ${versionRow.version}.`,
      });
      toast.success(`Restored ${versionRow.version} as active version.`);
      await refreshAfterSave(selectedPrompt.id);
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleImprove() {
    if (!aiInstr.trim()) {
      toast.error('Describe what you want to improve first.');
      return;
    }
    const curSys = detail?.system_prompt || '';
    const curUsr = detail?.user_prompt_template || '';
    setImproving(true);
    try {
      const desc = [
        `Improve the following ${component.toUpperCase()} prompt.`,
        '',
        `CURRENT SYSTEM PROMPT:\n${curSys.slice(0, 800)}`,
        '',
        `CURRENT USER PROMPT TEMPLATE:\n${curUsr.slice(0, 800)}`,
        '',
        `IMPROVEMENT INSTRUCTIONS:\n${aiInstr}`,
      ].join('\n');
      const result = await promptsService.aiSuggest({
        description: desc,
        model_choice: modelChoice,
      });
      setAiSuggestion(result);
      toast.success('Suggestion ready — review below.');
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setImproving(false);
    }
  }

  function handleClearSuggestion() {
    if (!aiSuggestion) return;
    setAiSuggestion(null);
    toast.success('Suggestion cleared.');
  }

  async function handleApplySuggestion() {
    if (!selectedPrompt?.id || !aiSuggestion) return;
    const vn = `v${(versions.length || 0) + 1}-ai`;
    setSaving(true);
    try {
      await promptsService.commitVersion(selectedPrompt.id, {
        version: vn,
        system_prompt: aiSuggestion.system_prompt || detail?.system_prompt || '',
        user_prompt_template: aiSuggestion.user_prompt_template || detail?.user_prompt_template || '',
        change_reason: 'Applied AI-generated improvement.',
      });
      toast.success(`AI suggestion applied as ${vn}.`);
      setAiSuggestion(null);
      setAiInstr('');
      await refreshAfterSave(selectedPrompt.id);
      setActiveTab('view');
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  function handleDownloadJson() {
    if (!detail) return;
    const payload = {
      component,
      prompt_name: detail.name,
      is_default: detail.is_default,
      active_version: detail.active_version || '—',
      system_prompt: detail.system_prompt,
      user_prompt_template: detail.user_prompt_template,
      owner: detail.owner || '—',
      updated_at: detail.updated_at || '—',
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const ver = (detail.active_version || 'v1').replace(/\//g, '-');
    downloadBlob(blob, `${component}_prompt_${ver}.json`);
  }

  function handleDownloadDoc() {
    const md = buildPromptDownloadMd({
      component,
      promptName: detail?.name,
      promptVersion: detail?.active_version,
      systemPrompt: detail?.system_prompt,
      userPromptTemplate: detail?.user_prompt_template,
    });
    const ver = (detail?.active_version || 'v1').replace(/\//g, '-');
    downloadBlob(new Blob([md], { type: 'application/msword' }), `prompt_${component}_${ver}.doc`);
  }

  const metaParts = selectedPrompt ? [
    `📦 ${selectedPrompt.name}`,
    `version: ${selectedPrompt.active_version || '—'}`,
    `component: ${selectedPrompt.component_type || component}`,
    selectedPrompt.is_default ? '🏷️ system default' : null,
    selectedPrompt.owner ? `owner: ${selectedPrompt.owner}` : null,
    selectedPrompt.updated_at ? `updated: ${formatMetaDate(selectedPrompt.updated_at)}` : null,
  ].filter(Boolean) : [];

  return (
    <section className={styles.section}>
      {showHeader && (
        <>
          <h2 className={styles.section__title}>Prompt Library</h2>
          <p className={styles.section__hint}>
            Create, edit, version, and improve prompt assets tagged{' '}
            <code>{component}</code>. These are available across all generation components.
          </p>
        </>
      )}

      <button
        type="button"
        className={styles.settingsToggle}
        onClick={() => setSettingsOpen((o) => !o)}
        aria-expanded={settingsOpen}
      >
        🔧 Prompt Settings {settingsOpen ? '▾' : '▸'}
      </button>

      {settingsOpen && (
        <div className={styles.settingsBody}>
          {promptsLoading ? (
            <div className={styles.center}><Loader /></div>
          ) : promptOptions.length === 0 ? (
            <p className={styles.warning}>
              No prompt assets found. Default assets are created on startup — try refreshing the page.
            </p>
          ) : (
            <>
              <Select
                label="Active Prompt"
                options={promptOptions}
                value={selectedKey}
                onChange={(e) => onSelectPrompt(e.target.value)}
              />
              {metaParts.length > 0 && (
                <p className={styles.meta}>{metaParts.join('  ·  ')}</p>
              )}

              <div className={styles.tabs} role="tablist">
                {PANEL_TABS.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={activeTab === t.id}
                    className={`${styles.tab} ${activeTab === t.id ? styles['tab--active'] : ''}`}
                    onClick={() => setActiveTab(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {loadingDetail && activeTab !== 'history' ? (
                <div className={styles.center}><Loader /></div>
              ) : (
                <>
                  {activeTab === 'view' && detail && (
                    <div className={styles.tabPanel}>
                      <div className={styles.promptCols}>
                        <div>
                          <label className={styles.fieldLabel}>System Prompt</label>
                          <textarea
                            className={`${styles.textarea} ${styles['textarea--readonly']}`}
                            rows={12}
                            readOnly
                            value={detail.system_prompt || ''}
                          />
                        </div>
                        <div>
                          <label className={styles.fieldLabel}>User Prompt Template</label>
                          <textarea
                            className={`${styles.textarea} ${styles['textarea--readonly']}`}
                            rows={12}
                            readOnly
                            value={detail.user_prompt_template || ''}
                          />
                        </div>
                      </div>
                      <p className={styles.versionCaption}>
                        Version <strong>{detail.active_version || '—'}</strong>
                        {detail.version_created_by && (
                          <> · committed by <code>{detail.version_created_by}</code></>
                        )}
                        {detail.version_created_at && (
                          <> · at <code>{formatMetaDate(detail.version_created_at)}</code></>
                        )}
                        {detail.version_change_reason && (
                          <> · <em>{detail.version_change_reason}</em></>
                        )}
                      </p>
                      <div className={styles.formActions}>
                        <Button variant="secondary" size="sm" onClick={handleDownloadJson}>
                          ⬇️ Download Active Version (JSON)
                        </Button>
                        <Button variant="ghost" size="sm" onClick={handleDownloadDoc}>
                          ⬇️ Download (.doc)
                        </Button>
                      </div>
                    </div>
                  )}

                  {activeTab === 'edit' && detail && (
                    <div className={styles.tabPanel}>
                      {selectedPrompt?.is_default && (
                        <p className={styles.infoNote}>
                          🏷️ <strong>{selectedPrompt.name}</strong> is the system default.
                          Your edits will be saved as a new version — v1 is preserved as the original.
                        </p>
                      )}
                      <form className={styles.form} onSubmit={(e) => { e.preventDefault(); handleSaveVersion(); }}>
                        <label className={styles.fieldLabel}>System Prompt</label>
                        <textarea
                          className={styles.textarea}
                          rows={8}
                          value={editForm.system}
                          onChange={(e) => setEditForm((f) => ({ ...f, system: e.target.value }))}
                        />
                        <label className={styles.fieldLabel}>User Prompt Template</label>
                        <textarea
                          className={styles.textarea}
                          rows={8}
                          value={editForm.user}
                          onChange={(e) => setEditForm((f) => ({ ...f, user: e.target.value }))}
                        />
                        <Input
                          label="Version Tag"
                          value={editForm.version}
                          onChange={(e) => setEditForm((f) => ({ ...f, version: e.target.value }))}
                        />
                        <Input
                          label="Change Log"
                          placeholder="Describe what changed in this version"
                          value={editForm.reason}
                          onChange={(e) => setEditForm((f) => ({ ...f, reason: e.target.value }))}
                        />
                        <div className={styles.formActions}>
                          <Button type="submit" variant="primary" size="sm" loading={saving}>
                            💾 Save as New Version
                          </Button>
                        </div>
                      </form>

                      <hr className={styles.divider} />
                      <p className={styles.subTitle}>➕ Create a New Prompt Asset for this Component</p>
                      <form className={styles.form} onSubmit={(e) => { e.preventDefault(); handleCreateAsset(); }}>
                        <Input
                          label="Asset Name"
                          required
                          placeholder={`e.g. custom_${component}_prompt`}
                          value={createForm.name}
                          onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
                        />
                        <Input
                          label="Description"
                          placeholder="What is this prompt for?"
                          value={createForm.description}
                          onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
                        />
                        <Input
                          label="Tags"
                          value={createForm.tags}
                          onChange={(e) => setCreateForm((f) => ({ ...f, tags: e.target.value }))}
                        />
                        <label className={styles.fieldLabel}>System Prompt</label>
                        <textarea
                          className={styles.textarea}
                          rows={6}
                          value={createForm.system}
                          onChange={(e) => setCreateForm((f) => ({ ...f, system: e.target.value }))}
                        />
                        <label className={styles.fieldLabel}>User Prompt Template</label>
                        <textarea
                          className={styles.textarea}
                          rows={6}
                          value={createForm.user}
                          onChange={(e) => setCreateForm((f) => ({ ...f, user: e.target.value }))}
                        />
                        <div className={styles.formActions}>
                          <Button type="submit" variant="primary" size="sm" loading={saving}>
                            🚀 Create & Activate
                          </Button>
                        </div>
                      </form>
                    </div>
                  )}

                  {activeTab === 'history' && (
                    <div className={styles.tabPanel}>
                      {loadingVersions ? (
                        <div className={styles.center}><Loader /></div>
                      ) : versions.length === 0 ? (
                        <p className={styles.versionCaption}>No versions recorded yet.</p>
                      ) : (
                        <ul className={styles.versionList}>
                          {versions.map((v) => {
                            const expanded = expandedVersionId === v.id;
                            return (
                              <li key={v.id} className={styles.versionCard}>
                                <button
                                  type="button"
                                  className={styles.versionCard__head}
                                  onClick={() => setExpandedVersionId(expanded ? null : v.id)}
                                >
                                  <span className={styles.versionTag}>{v.version}</span>
                                  {v.is_active && <span className={styles.badgeActive}>✅ active</span>}
                                  <span>{v.created_by || selectedPrompt?.owner || '—'}</span>
                                  <span>{formatMetaDate(v.created_at)}</span>
                                  <span>{expanded ? '▾' : '▸'}</span>
                                </button>
                                {expanded && (
                                  <div className={styles.versionCard__body}>
                                    {v.change_reason && (
                                      <p className={styles.changeReason}>📝 {v.change_reason}</p>
                                    )}
                                    <div className={styles.promptCols}>
                                      <div>
                                        <label className={styles.fieldLabel}>System Prompt</label>
                                        <textarea
                                          className={`${styles.textarea} ${styles['textarea--readonly']}`}
                                          rows={8}
                                          readOnly
                                          value={v.system_prompt || ''}
                                        />
                                      </div>
                                      <div>
                                        <label className={styles.fieldLabel}>User Prompt Template</label>
                                        <textarea
                                          className={`${styles.textarea} ${styles['textarea--readonly']}`}
                                          rows={8}
                                          readOnly
                                          value={v.user_prompt_template || ''}
                                        />
                                      </div>
                                    </div>
                                    {!v.is_active && (
                                      <Button
                                        variant="secondary"
                                        size="sm"
                                        loading={saving}
                                        onClick={() => handleRestoreVersion(v)}
                                      >
                                        ↩️ Restore {v.version}
                                      </Button>
                                    )}
                                  </div>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  )}

                  {activeTab === 'ai' && (
                    <div className={styles.tabPanel}>
                      <p className={styles.versionCaption}>
                        Describe how to improve this prompt and the AI will generate a revised version.
                        Review it before applying.
                      </p>
                      <label className={styles.fieldLabel}>Improvement Instructions</label>
                      <textarea
                        className={styles.textarea}
                        rows={4}
                        placeholder="e.g. Make the system prompt more concise. Emphasise real-world examples. Remove jargon."
                        value={aiInstr}
                        onChange={(e) => setAiInstr(e.target.value)}
                      />
                      <div className={styles.aiActionRow}>
                        <Button
                          variant="secondary"
                          size="sm"
                          fullWidth
                          loading={improving}
                          onClick={handleImprove}
                        >
                          ✨ Improve with AI
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          fullWidth
                          onClick={handleClearSuggestion}
                          disabled={!aiSuggestion}
                          title={aiSuggestion ? 'Discard the AI suggestion preview' : 'No suggestion to clear'}
                        >
                          🗑️ Clear Suggestion
                        </Button>
                      </div>
                      {aiSuggestion && (
                        <div className={styles.aiSuggest}>
                          <div className={styles.promptCols}>
                            <div>
                              <label className={styles.fieldLabel}>Suggested System Prompt</label>
                              <textarea
                                className={styles.textarea}
                                rows={8}
                                value={aiSuggestion.system_prompt || ''}
                                onChange={(e) => setAiSuggestion((s) => ({
                                  ...s,
                                  system_prompt: e.target.value,
                                }))}
                              />
                            </div>
                            <div>
                              <label className={styles.fieldLabel}>Suggested User Prompt Template</label>
                              <textarea
                                className={styles.textarea}
                                rows={8}
                                value={aiSuggestion.user_prompt_template || ''}
                                onChange={(e) => setAiSuggestion((s) => ({
                                  ...s,
                                  user_prompt_template: e.target.value,
                                }))}
                              />
                            </div>
                          </div>
                          <Button
                            variant="primary"
                            size="sm"
                            loading={saving}
                            onClick={handleApplySuggestion}
                          >
                            ✅ Apply Suggestion as New Version
                          </Button>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
