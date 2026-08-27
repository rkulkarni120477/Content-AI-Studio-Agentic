import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { selectIsAdmin, selectIsReviewer } from '@features/auth/authSlice';
import { fetchPromptsThunk } from '@features/prompts/promptsThunks';
import { commitPromptThunk } from '@features/prompts/promptsThunks';
import { promptsService } from '@features/prompts/services/promptsService';
import PromptCapabilityNotices from '@components/generation/PromptCapabilityNotices/PromptCapabilityNotices';
import { adminService } from '@features/admin/services/adminService';
import { selectPrompts, selectPromptsLoading } from '@features/prompts/promptsSlice';
import {
  selectModelChoice,
  selectExpertDomain,
  selectTargetAudience,
  selectAudienceCategory,
  selectSelectedProject,
  selectSelectedCluster,
  selectSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import {
  CDD_DEFAULT_SYSTEM,
  CDD_DEFAULT_USER,
  BLUEPRINT_DEFAULT_SYSTEM,
  BLUEPRINT_DEFAULT_USER,
  GENERATE_DEFAULT_SYSTEM,
  GENERATE_DEFAULT_USER,
  promptDisplayLabel,
  buildPromptDownloadMd,
  componentLabel,
} from '@utils/promptDefaults';
import { downloadBlob, extractErrorMessage } from '@utils/helpers';
import { useLabels } from '@hooks/useLabels';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import Loader from '@components/common/Loader/Loader';
import PromptDetailsModal from '@components/generation/PromptDetailsModal/PromptDetailsModal';
import styles from './InlinePromptControls.module.scss';

const DEFAULTS = {
  cdd: { system: CDD_DEFAULT_SYSTEM, user: CDD_DEFAULT_USER },
  blueprint: { system: BLUEPRINT_DEFAULT_SYSTEM, user: BLUEPRINT_DEFAULT_USER },
  generate: { system: GENERATE_DEFAULT_SYSTEM, user: GENERATE_DEFAULT_USER },
};

function nextPromptVersion(activeVersion) {
  const n = parseInt(String(activeVersion || '1').replace(/\D/g, ''), 10);
  return `v${(Number.isNaN(n) ? 0 : n) + 1}`;
}

export default function InlinePromptControls({
  component = 'cdd',
  extraInstructions = '',
  onExtraInstructionsChange,
  showExtraInstructions = false,
  onPromptsChange,
  // Lets the page place the block-wide half of the reconciliation beside the
  // block-wide Generate button instead of here, above the single-call one.
  onCapabilityChange,
  headerHint,
  embedded = false,
}) {
  const dispatch = useAppDispatch();
  const prompts = useAppSelector(selectPrompts);
  const promptsLoading = useAppSelector(selectPromptsLoading);
  const modelChoice = useAppSelector(selectModelChoice);
  const expertDomain = useAppSelector(selectExpertDomain);
  const targetAudience = useAppSelector(selectTargetAudience);
  const audienceCategory = useAppSelector(selectAudienceCategory);
  const selProject = useAppSelector(selectSelectedProject);
  const selCluster = useAppSelector(selectSelectedCluster);
  const selCourse = useAppSelector(selectSelectedCourse);
  const isAdmin = useAppSelector(selectIsAdmin);
  // prompts.create / prompts.manage are admin+reviewer only — authors go
  // through 📬 Request a Change instead of seeing buttons that would 403.
  const canManagePrompts = useAppSelector(selectIsReviewer);
  const L = useLabels();

  const [selectedKey, setSelectedKey] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [userPrompt, setUserPrompt] = useState('');
  const [overrideSystem, setOverrideSystem] = useState(null);
  const [overrideUser, setOverrideUser] = useState(null);

  const [showView, setShowView] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showImprove, setShowImprove] = useState(false);
  const [showSaveInstr, setShowSaveInstr] = useState(false);
  const [aiSuggestion, setAiSuggestion] = useState(null);

  const [createForm, setCreateForm] = useState({ name: '', description: '', system: '', user: '' });
  const [editForm, setEditForm] = useState({ version: 'v2', reason: '', system: '', user: '' });
  const [improveInstr, setImproveInstr] = useState('');
  const [improving, setImproving] = useState(false);
  const [saveInstrForm, setSaveInstrForm] = useState({ name: '', asNewVersion: false });

  const [savedInstrs, setSavedInstrs] = useState([]);
  const [loadInstrSel, setLoadInstrSel] = useState('— Start fresh —');
  const [loadingDetail, setLoadingDetail] = useState(false);
  // Reconciliation of the selected template against what the pipeline emits, served
  // with the prompt detail (no extra round trip). Null means "not applicable" — the
  // API omits it for components that never produce a day table — so it must never be
  // rendered as "no problems found".
  const [capability, setCapability] = useState(null);
  // Held in a ref, not a dependency: loadPromptDetail feeds a useEffect dep array, so
  // a caller passing an inline arrow would re-create the callback every render and
  // re-fetch the prompt detail in a loop. The ref keeps the latest callback without
  // making the identity of loadPromptDetail depend on the caller's render.
  const onCapabilityChangeRef = useRef(onCapabilityChange);
  onCapabilityChangeRef.current = onCapabilityChange;

  const compLabel = componentLabel(component, L);
  const defaults = DEFAULTS[component] || { system: '', user: '' };
  const navigate = useNavigate();

  const filteredPrompts = useMemo(
    () => (prompts || []).filter(
      (p) => !p.component_type || p.component_type === component,
    ),
    [prompts, component],
  );

  const labelToPrompt = useMemo(() => {
    const map = new Map();
    filteredPrompts.forEach((p) => {
      let lbl = promptDisplayLabel(p, L);
      if (map.has(lbl)) lbl = `${lbl} (${p.id})`;
      map.set(lbl, p);
    });
    return map;
  }, [filteredPrompts, L]);

  const promptOptions = useMemo(() => {
    if (labelToPrompt.size === 0) return [];
    return Array.from(labelToPrompt.keys());
  }, [labelToPrompt]);

  const selectedPrompt = selectedKey ? labelToPrompt.get(selectedKey) : null;

  const effectiveSystem = overrideSystem ?? systemPrompt;
  const effectiveUser = overrideUser ?? userPrompt;
  const hasOverride = overrideSystem !== null || overrideUser !== null;

  const notifyParent = useCallback((sys, usr, extra, isOverride) => {
    onPromptsChange?.({
      systemPrompt: sys,
      userPromptTemplate: usr,
      extraInstructions: extra ?? extraInstructions,
      // True only when the user explicitly applied an ad-hoc override (e.g. "Use Now"
      // on an AI suggestion) — NOT when a library default merely auto-loaded. Callers
      // must use this to decide whether to send system_prompt_override/user_prompt_override;
      // sending the raw auto-loaded default bypasses all real context-building server-side.
      hasOverride: Boolean(isOverride),
      // Identity of the currently-selected library prompt. Callers send
      // selectedPromptId as `prompt_id` when the user picked a non-default
      // template (isDefault === false), so that specific pipeline prompt drives
      // generation server-side (rendered through the normal variable path).
      selectedPromptId: selectedPrompt?.id ?? null,
      isDefault: Boolean(selectedPrompt?.is_default),
    });
  }, [onPromptsChange, extraInstructions, selectedPrompt]);

  const loadPromptDetail = useCallback(async (promptId) => {
    if (!promptId) {
      setSystemPrompt(defaults.system);
      setUserPrompt(defaults.user);
      setCapability(null); onCapabilityChangeRef.current?.(null);
      return;
    }
    setLoadingDetail(true);
    try {
      const detail = await promptsService.getPromptDetail(promptId);
      setSystemPrompt(detail.system_prompt || defaults.system);
      setUserPrompt(detail.user_prompt_template || defaults.user);
      setCapability(detail.capability || null); onCapabilityChangeRef.current?.(detail.capability || null);
    } catch {
      setSystemPrompt(defaults.system);
      setUserPrompt(defaults.user);
      // Cleared, not left stale: notices from the PREVIOUS template would be read as
      // describing the one now selected.
      setCapability(null); onCapabilityChangeRef.current?.(null);
    } finally {
      setLoadingDetail(false);
    }
  }, [defaults.system, defaults.user]);

  useEffect(() => {
    // project_id only matters for a platform admin (see list_prompts) — it
    // scopes their dropdown to the tenant/course they're currently working
    // in instead of every tenant's prompts; a tenant user is always scoped
    // to their own project regardless. Refetches on tenant-context change
    // too, so switching project mid-session doesn't leave a stale list.
    dispatch(fetchPromptsThunk({ component, project_id: selProject?.id }));
  }, [dispatch, component, selProject?.id]);

  useEffect(() => {
    async function loadSaved() {
      if (!selProject?.id && !selCourse?.id) return;
      try {
        const list = await adminService.listInstructions({
          component,
          project_id: selProject?.id,
          course_id: selCourse?.id,
        });
        setSavedInstrs(list || []);
      } catch {
        setSavedInstrs([]);
      }
    }
    loadSaved();
  }, [component, selProject?.id, selCourse?.id]);

  useEffect(() => {
    if (promptOptions.length === 0) {
      setSelectedKey('');
      setSystemPrompt(defaults.system);
      setUserPrompt(defaults.user);
      return;
    }
    if (!selectedKey || !labelToPrompt.has(selectedKey)) {
      const def = filteredPrompts.find((p) => p.is_default) || filteredPrompts[0];
      const lbl = promptDisplayLabel(def, L);
      const key = labelToPrompt.has(lbl) ? lbl : promptOptions[0];
      setSelectedKey(key);
      loadPromptDetail(labelToPrompt.get(key)?.id);
    }
  }, [promptOptions, labelToPrompt, filteredPrompts, defaults, loadPromptDetail, selectedKey]);

  useEffect(() => {
    notifyParent(effectiveSystem, effectiveUser, extraInstructions, hasOverride);
  }, [effectiveSystem, effectiveUser, extraInstructions, hasOverride, notifyParent]);

  function onSelectPrompt(label) {
    setSelectedKey(label);
    setOverrideSystem(null);
    setOverrideUser(null);
    setAiSuggestion(null);
    const p = labelToPrompt.get(label);
    loadPromptDetail(p?.id);
  }

  function openEditFromView() {
    setEditForm({
      version: nextPromptVersion(selectedPrompt?.active_version),
      reason: '',
      system: effectiveSystem,
      user: effectiveUser,
    });
    setShowEdit(true);
    setShowCreate(false);
    setShowImprove(false);
  }

  async function handleSaveInstruction() {
    if (!saveInstrForm.name.trim()) {
      toast.error('Name the instructions first.');
      return;
    }
    if (!extraInstructions.trim()) {
      toast.error('There are no additional instructions to save.');
      return;
    }
    try {
      await adminService.saveInstruction({
        name: saveInstrForm.name.trim(),
        component,
        content: extraInstructions.trim(),
        project_id: selProject?.id,
        cluster_id: selCluster?.id,
        course_id: selCourse?.id,
        as_new_version: saveInstrForm.asNewVersion,
      });
      toast.success(`Instructions saved as "${saveInstrForm.name.trim()}".`);
      setShowSaveInstr(false);
      setSaveInstrForm({ name: '', asNewVersion: false });
      const list = await adminService.listInstructions({
        component,
        project_id: selProject?.id,
        course_id: selCourse?.id,
      });
      setSavedInstrs(list || []);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleCreatePrompt() {
    if (!createForm.name.trim()) {
      toast.error('Asset name is required.');
      return;
    }
    try {
      await dispatch(commitPromptThunk({
        name: createForm.name,
        description: createForm.description,
        component_type: component,
        system_prompt: createForm.system,
        user_prompt_template: createForm.user,
        project_id: selProject?.id,
      })).unwrap();
      toast.success(`"${createForm.name.trim()}" created.`);
      setShowCreate(false);
      dispatch(fetchPromptsThunk({ component, project_id: selProject?.id }));
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleSaveEditVersion() {
    if (!selectedPrompt?.id) return;
    if (!editForm.version.trim()) {
      toast.error('Version tag is required.');
      return;
    }
    try {
      await promptsService.commitVersion(selectedPrompt.id, {
        version: editForm.version,
        system_prompt: editForm.system,
        user_prompt_template: editForm.user,
        change_reason: editForm.reason || 'Edited via inline controls.',
      });
      toast.success(isAdmin
        ? `Version ${editForm.version} saved and deployed.`
        : `Version ${editForm.version} saved as a draft — it deploys after approval.`);
      setShowEdit(false);
      dispatch(fetchPromptsThunk({ component, project_id: selProject?.id }));
      loadPromptDetail(selectedPrompt.id);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleImprove() {
    if (!improveInstr.trim()) return;
    setImproving(true);
    try {
      const desc = `Improve this prompt. Current system: ${effectiveSystem.slice(0, 500)}. Current user: ${effectiveUser.slice(0, 500)}. Instructions: ${improveInstr}`;
      const result = await promptsService.aiSuggest({ description: desc, model_choice: modelChoice });
      setAiSuggestion({
        system_prompt: result.system_prompt || effectiveSystem,
        user_prompt_template: result.user_prompt_template || effectiveUser,
      });
      setShowImprove(false);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    } finally {
      setImproving(false);
    }
  }

  function applyUseNow() {
    setOverrideSystem(aiSuggestion.system_prompt);
    setOverrideUser(aiSuggestion.user_prompt_template);
    setAiSuggestion(null);
  }

  const instrOptions = [
    { value: '— Start fresh —', label: '— Start fresh —' },
    ...savedInstrs.map((r) => ({ value: r.name, label: r.name })),
  ];

  return (
    <section className={embedded ? styles.panelEmbedded : styles.panel}>
      {headerHint && <p className={styles.hint}>{headerHint}</p>}

      {!embedded && (
        <h3 className={styles.panel__header}>🎯 {compLabel} Prompts</h3>
      )}

      {promptsLoading || loadingDetail ? (
        <div className={styles.center}><Loader /></div>
      ) : (
        <>
          <div className={styles.selectorRow}>
            <div className={styles.selectorRow__main}>
              <Select
                label="Prompt Template"
                options={promptOptions.length
                  ? promptOptions.map((l) => ({ value: l, label: l }))
                  : [{ value: '', label: '— No prompts in library —' }]}
                value={selectedKey}
                onChange={(e) => onSelectPrompt(e.target.value)}
                disabled={promptOptions.length === 0}
              />
              {selectedPrompt && (
                <p className={styles.meta}>
                  📦 <strong>{selectedPrompt.name}</strong>
                  {' · '}v<code>{selectedPrompt.active_version || '—'}</code>
                  {selectedPrompt.is_default && ' · 🏷️ default'}
                  {selectedPrompt.owner && ` · owner: ${selectedPrompt.owner}`}
                  {selectedPrompt.updated_at && (
                    <> · updated: <code>{new Date(selectedPrompt.updated_at).toISOString().slice(0, 10)}</code></>
                  )}
                </p>
              )}
            </div>
            <Button variant="secondary" size="sm" onClick={() => setShowView(true)} disabled={!selectedPrompt}>
              👁 View
            </Button>
          </div>

          <PromptCapabilityNotices capability={capability} scope="single" />

          {hasOverride && (
            <div className={styles.overrideBanner}>
              <span>🤖 AI-improved prompt active — used for this generation only.</span>
              <Button variant="ghost" size="xs" onClick={() => { setOverrideSystem(null); setOverrideUser(null); }}>
                ↩️ Revert
              </Button>
            </div>
          )}

          {aiSuggestion && (
            <div className={styles.aiPanel}>
              <p className={styles.aiPanel__title}>✨ AI Improvement Ready</p>
              <div className={styles.aiPanel__cols}>
                <div>
                  <label className={styles.fieldLabel}>System Prompt</label>
                  <textarea
                    className={styles.textarea}
                    rows={6}
                    value={aiSuggestion.system_prompt}
                    onChange={(e) => setAiSuggestion((s) => ({ ...s, system_prompt: e.target.value }))}
                  />
                </div>
                <div>
                  <label className={styles.fieldLabel}>User Prompt Template</label>
                  <textarea
                    className={styles.textarea}
                    rows={6}
                    value={aiSuggestion.user_prompt_template}
                    onChange={(e) => setAiSuggestion((s) => ({ ...s, user_prompt_template: e.target.value }))}
                  />
                </div>
              </div>
              <div className={styles.aiPanel__actions}>
                <Button variant="primary" size="sm" onClick={applyUseNow}>✅ Use Now</Button>
                <Button variant="ghost" size="sm" onClick={() => setAiSuggestion(null)}>🗑️ Discard</Button>
              </div>
            </div>
          )}

          {showExtraInstructions && (
            <div className={styles.extraBlock}>
              <label className={styles.fieldLabel}>
                💬 Additional Instructions <span className={styles.optional}>(optional)</span>
              </label>
              {savedInstrs.length > 0 && (
                <div className={styles.instrLoad}>
                  <Select
                    label=""
                    options={instrOptions}
                    value={loadInstrSel}
                    onChange={(e) => setLoadInstrSel(e.target.value)}
                  />
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      if (loadInstrSel === '— Start fresh —') {
                        onExtraInstructionsChange?.('');
                      } else {
                        const rec = savedInstrs.find((r) => r.name === loadInstrSel);
                        if (rec) onExtraInstructionsChange?.(rec.content);
                      }
                    }}
                  >
                    📥 Load
                  </Button>
                </div>
              )}
              <textarea
                className={styles.textarea}
                rows={4}
                placeholder="e.g. Focus on clinical simulation. Include DEI examples."
                value={extraInstructions}
                onChange={(e) => onExtraInstructionsChange?.(e.target.value)}
              />
            </div>
          )}

          <div className={styles.actionRow}>
            <Button variant="secondary" size="sm" onClick={() => { setShowImprove(!showImprove); setShowCreate(false); setShowEdit(false); }}>
              🤖 Improve with AI
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setShowSaveInstr(!showSaveInstr)}>
              💾 Save Instructions
            </Button>
          </div>
          <div className={styles.actionRow}>
            {canManagePrompts && (
              <Button variant="ghost" size="sm" onClick={() => { setShowCreate(!showCreate); setShowEdit(false); setShowImprove(false); }}>
                ➕ New Prompt
              </Button>
            )}
            {canManagePrompts && (
              <Button variant="ghost" size="sm" onClick={() => {
                setEditForm({
                  version: nextPromptVersion(selectedPrompt?.active_version),
                  reason: '',
                  system: effectiveSystem,
                  user: effectiveUser,
                });
                setShowEdit(!showEdit);
                setShowCreate(false);
                setShowImprove(false);
              }} disabled={!selectedPrompt}>
                ✏️ Edit Prompt
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                const md = buildPromptDownloadMd({
                  projectName: selProject?.name,
                  clusterName: selCluster?.name,
                  courseName: selCourse?.name,
                  component,
                  promptName: selectedPrompt?.name,
                  promptVersion: selectedPrompt?.active_version,
                  systemPrompt: effectiveSystem,
                  userPromptTemplate: effectiveUser,
                  extraInstructions,
                  isAiOverride: hasOverride,
                  labels: L,
                });
                const ver = (selectedPrompt?.active_version || 'v1').replace(/\//g, '-');
                downloadBlob(new Blob([md], { type: 'application/msword' }), `prompt_${component}_${ver}.doc`);
              }}
            >
              ⬇️ Download
            </Button>
            <Button
              variant="ghost"
              size="sm"
              title="Ask the prompt admins for a new prompt or a change to this one"
              onClick={() => {
                // Deep-link into the Prompt Library request form with the
                // CAS context prefilled (Phase 12d).
                const qs = new URLSearchParams({ component });
                const ctx = [selProject?.name, selCourse?.name].filter(Boolean).join(' / ');
                if (ctx) qs.set('context', ctx);
                if (selectedPrompt?.id) qs.set('promptId', selectedPrompt.id);
                navigate(`/prompt-library/requests/new?${qs.toString()}`);
              }}
            >
              📬 Request a Change
            </Button>
          </div>

          {showSaveInstr && (
            <div className={styles.subPanel}>
              <p className={styles.subPanel__title}>Name and save instructions for reuse</p>
              <div className={styles.subPanel__row}>
                <Input
                  placeholder="e.g. Clinical simulation focus"
                  value={saveInstrForm.name}
                  onChange={(e) => setSaveInstrForm((f) => ({ ...f, name: e.target.value }))}
                />
                <label className={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={saveInstrForm.asNewVersion}
                    onChange={(e) => setSaveInstrForm((f) => ({ ...f, asNewVersion: e.target.checked }))}
                  />
                  New version
                </label>
                <Button variant="primary" size="sm" onClick={handleSaveInstruction}>Confirm</Button>
              </div>
            </div>
          )}

          {showCreate && (
            <div className={styles.subPanel}>
              <p className={styles.subPanel__title}>➕ Create New {compLabel} Prompt</p>
              <Input label="Asset Name *" value={createForm.name} onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))} />
              <Input label="Description" value={createForm.description} onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))} />
              <label className={styles.fieldLabel}>System Prompt</label>
              <textarea className={styles.textarea} rows={5} value={createForm.system || effectiveSystem} onChange={(e) => setCreateForm((f) => ({ ...f, system: e.target.value }))} />
              <label className={styles.fieldLabel}>User Prompt Template</label>
              <textarea className={styles.textarea} rows={5} value={createForm.user || effectiveUser} onChange={(e) => setCreateForm((f) => ({ ...f, user: e.target.value }))} />
              <div className={styles.subPanel__row}>
                <Button variant="primary" size="sm" onClick={handleCreatePrompt}>🚀 Create & Activate</Button>
                <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>Cancel</Button>
              </div>
            </div>
          )}

          {showEdit && selectedPrompt && (
            <div className={styles.subPanel}>
              <p className={styles.subPanel__title}>✏️ Edit: {selectedPrompt.name}</p>
              {selectedPrompt.is_default && (
                <p className={styles.infoNote}>🏷️ System default — edits save as a new version; v1 is preserved.</p>
              )}
              <label className={styles.fieldLabel}>System Prompt</label>
              <textarea className={styles.textarea} rows={6} value={editForm.system} onChange={(e) => setEditForm((f) => ({ ...f, system: e.target.value }))} />
              <label className={styles.fieldLabel}>User Prompt Template</label>
              <textarea className={styles.textarea} rows={6} value={editForm.user} onChange={(e) => setEditForm((f) => ({ ...f, user: e.target.value }))} />
              <Input label="Version Tag" value={editForm.version} onChange={(e) => setEditForm((f) => ({ ...f, version: e.target.value }))} />
              <Input label="Change Log" value={editForm.reason} onChange={(e) => setEditForm((f) => ({ ...f, reason: e.target.value }))} />
              <div className={styles.subPanel__row}>
                <Button variant="primary" size="sm" onClick={handleSaveEditVersion}>💾 Save as New Version</Button>
                <Button variant="ghost" size="sm" onClick={() => setShowEdit(false)}>Cancel</Button>
              </div>
            </div>
          )}

          {showImprove && (
            <div className={styles.subPanel}>
              <p className={styles.subPanel__title}>🤖 Improve Prompt with AI</p>
              <textarea
                className={styles.textarea}
                rows={3}
                placeholder="e.g. Make the system prompt more concise. Emphasise real-world examples."
                value={improveInstr}
                onChange={(e) => setImproveInstr(e.target.value)}
              />
              <Button variant="primary" size="sm" loading={improving} onClick={handleImprove}>
                ✨ Generate Improvement
              </Button>
            </div>
          )}
        </>
      )}

      <PromptDetailsModal
        open={showView}
        onClose={() => setShowView(false)}
        component={component}
        promptMeta={selectedPrompt}
        systemPrompt={effectiveSystem}
        userPrompt={effectiveUser}
        extraInstructions={extraInstructions}
        isAiOverride={hasOverride}
        projectName={selProject?.name}
        clusterName={selCluster?.name}
        courseName={selCourse?.name}
        onEditPrompt={canManagePrompts ? openEditFromView : undefined}
      />
    </section>
  );
}
