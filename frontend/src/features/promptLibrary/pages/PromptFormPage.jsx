import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  checkDuplicate,
  deleteAttachment,
  fetchPrompt,
  updatePrompt,
  uploadAttachment,
} from '../api/prompts';
import {
  commitPipelineVersion,
  createPipelinePrompt,
  setPipelineVariables,
  updatePipelineMeta,
} from '../api/pipeline';
import { fetchTeams } from '../api/teams';
import { useToast } from '../context/ToastContext';
import { canManagePipelinePrompts } from '../utils/permissions';
import { useAuth } from '../context/AuthContext';
import { platformService } from '@features/platform/services/platformService';
import TeamMultiSelect from '../components/TeamMultiSelect';
import { extractVarNames, findLegacyVarNames, toLabel } from '../utils/prompt';
import { useLabels } from '@hooks/useLabels';
import { APPLY_PROPOSAL_KEY } from '../utils/requestProposal';
import { plCourses, plHome, plPrompt } from '../paths';

export default function PromptFormPage() {
  const L = useLabels();
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();
  // "Draft this prompt" on an admin request deep-links here with the request
  // context prefilled into the description (create mode only).
  const [searchParams] = useSearchParams();
  const { show } = useToast();

  const [teamOptions, setTeamOptions] = useState([]);
  const [parentId, setParentId] = useState('');
  const [loadedPrompt, setLoadedPrompt] = useState(null);
  const [loading, setLoading] = useState(isEdit);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [description, setDescription] = useState(() => (isEdit ? '' : searchParams.get('description') || ''));
  const [category, setCategory] = useState('');
  const [tags, setTags] = useState('');
  const [visibility, setVisibility] = useState('draft');
  const [selectedTeamIds, setSelectedTeamIds] = useState([]);
  const [createVersion, setCreateVersion] = useState(false);
  const [versionNote, setVersionNote] = useState('');
  const [varDefs, setVarDefs] = useState({});
  const [attachments, setAttachments] = useState([]);
  const [saving, setSaving] = useState(false);

  // Pipeline-kind state (Phase 7b dual editor; since 12b creation is
  // pipeline-only — the library branch remains for EDITING legacy rows).
  const [systemPrompt, setSystemPrompt] = useState('');
  const [componentType, setComponentType] = useState('');
  const [variant, setVariant] = useState('');
  const [changeNote, setChangeNote] = useState('');
  // Declared variables (strict enforcement): name -> {declared, label, hint}.
  const [pipeVarDefs, setPipeVarDefs] = useState({});

  const { user } = useAuth();
  const canPipeline = canManagePipelinePrompts(user);
  // Creation is always pipeline (all prompts are CAS prompts now); editing
  // follows the loaded row's kind so the 20 legacy library rows stay editable.
  const isPipeline = isEdit ? loadedPrompt?.prompt_kind === 'pipeline' : true;

  // Tenant scope — platform-admin only, create only. A platform admin's own
  // project is always null, so a pipeline prompt they create here landed
  // shared/global (visible to every tenant) unless explicitly stamped with a
  // tenant — see createPipelinePrompt. A tenant-scoped admin is always
  // stamped with their own project server-side regardless, so this control
  // would do nothing for them and stays hidden. Not offered on edit: the
  // registry has no endpoint to re-scope an existing row's project_id.
  const isPlatformAdmin = Boolean(user?.is_platform_admin);
  const [tenantOptions, setTenantOptions] = useState([]);
  const [tenantId, setTenantId] = useState('');

  useEffect(() => {
    if (!isPlatformAdmin || isEdit) return;
    platformService.listTenants()
      .then((tenants) => setTenantOptions((tenants || []).map((t) => ({
        value: String(t.id),
        label: t.slug ? `${t.name} (${t.slug})` : t.name,
      }))))
      .catch(() => {});
  }, [isPlatformAdmin, isEdit]);

  useEffect(() => {
    void fetchTeams().then(setTeamOptions);
  }, []);

  useEffect(() => {
    if (!isEdit || !id) return;
    setLoading(true);
    fetchPrompt(id)
      .then((p) => {
        if (!p) {
          navigate(plHome, { replace: true });
          return;
        }
        setTitle(p.title);
        setContent(p.content);
        setDescription(p.description || '');
        setCategory(p.category || '');
        setTags((p.tags || []).join(', '));
        setVisibility(p.visibility || 'draft');
        setSelectedTeamIds(p.teams || []);
        setParentId(p.parent_id || '');
        setLoadedPrompt(p);
        setAttachments(p.attachments || []);
        if (p.prompt_kind === 'pipeline') {
          setSystemPrompt(p.pipeline?.system_prompt || '');
          setComponentType(p.pipeline?.component_type || '');
          setVariant(p.pipeline?.variant || '');
          // A request's proposed edit, handed off by the admin request page
          // ("Apply this proposal in the editor"). Consumed once, then cleared.
          try {
            const raw = sessionStorage.getItem(APPLY_PROPOSAL_KEY);
            const proposal = raw ? JSON.parse(raw) : null;
            if (proposal && String(proposal.promptId) === String(p.id)) {
              sessionStorage.removeItem(APPLY_PROPOSAL_KEY);
              if (proposal.system != null) setSystemPrompt(proposal.system);
              if (proposal.user != null) setContent(proposal.user);
              if (proposal.note) setChangeNote(proposal.note);
              show('Proposal loaded — review the text, then save to apply it.');
            }
          } catch {
            sessionStorage.removeItem(APPLY_PROPOSAL_KEY);
          }
          const pipeDefs = {};
          (p.variables || []).forEach((v) => {
            pipeDefs[v.name] = { declared: true, label: v.label || '', hint: v.hint || '' };
          });
          setPipeVarDefs(pipeDefs);
        }
        const defs = {};
        (p.variables || []).forEach((v) => {
          defs[v.name] = { label: v.label || '', hint: v.hint || '' };
        });
        setVarDefs(defs);
      })
      .finally(() => setLoading(false));
  }, [id, isEdit, navigate]);

  useEffect(() => {
    const names = extractVarNames(content);
    setVarDefs((prev) => {
      const next = {};
      names.forEach((n) => {
        next[n] = prev[n] || { label: toLabel(n), hint: '' };
      });
      return next;
    });
  }, [content]);

  const variables = Object.entries(varDefs).map(([name, d]) => ({
    name,
    label: d.label || toLabel(name),
    hint: d.hint || '',
  }));

  const legacyVars = findLegacyVarNames(isPipeline ? `${systemPrompt}\n${content}` : content);

  // Placeholders in the current pipeline text, plus any stale declared names
  // (declared on the row but no longer in the text — flagged in the editor).
  const pipeNames = isPipeline ? extractVarNames(`${systemPrompt}\n${content}`) : [];
  const pipeNamesUnion = [
    ...pipeNames,
    ...Object.keys(pipeVarDefs).filter((n) => pipeVarDefs[n].declared && !pipeNames.includes(n)),
  ];

  function pipeDeclaredList() {
    return pipeNamesUnion
      .filter((n) => pipeVarDefs[n]?.declared)
      .map((n) => ({
        name: n,
        label: pipeVarDefs[n].label || '',
        hint: pipeVarDefs[n].hint || '',
      }));
  }

  // Version tags follow the numeric ladder ("v3"); the serializer's numeric
  // `version` field is version_number, so max+1 is always fresh.
  function nextVersionTag(p) {
    const nums = (p?.versions || []).map((v) => v.version || 0);
    return `v${(nums.length ? Math.max(...nums) : 0) + 1}`;
  }

  async function handlePipelineSubmit() {
    setSaving(true);
    try {
      if (!isEdit) {
        const created = await createPipelinePrompt({
          name: title.trim(),
          description: description.trim(),
          componentType: componentType || null,
          variant: variant || null,
          systemPrompt: systemPrompt,
          userPromptTemplate: content,
          changeReason: changeNote.trim() || 'Created via console.',
          projectId: isPlatformAdmin && tenantId ? Number(tenantId) : null,
        });
        show('Pipeline prompt created!');
        navigate(plPrompt(created.id));
        return;
      }
      const pipe = loadedPrompt?.pipeline || {};
      const rekeyChanged =
        componentType !== (pipe.component_type || '') || variant !== (pipe.variant || '');
      const descChanged = description.trim() !== (loadedPrompt?.description || '');
      if (rekeyChanged || descChanged) {
        // Send component/variant only when actually changed — re-keying is
        // admin-gated and 422s on default rows.
        await updatePipelineMeta(id, {
          ...(descChanged ? { description: description.trim() } : {}),
          ...(rekeyChanged ? { componentType, variant } : {}),
        });
      }
      const contentChanged =
        content !== (loadedPrompt?.content ?? '') || systemPrompt !== (pipe.system_prompt ?? '');
      if (contentChanged) {
        await commitPipelineVersion(id, {
          version: nextVersionTag(loadedPrompt),
          systemPrompt,
          userPromptTemplate: content,
          changeReason: changeNote.trim() || 'Edited via console.',
        });
      }
      // Declared variables — after the version commit so the server validates
      // names against the newly deployed text (admin instant-deploy).
      if (canPipeline) {
        const declared = pipeDeclaredList();
        const orig = (loadedPrompt?.variables || []).map((v) => ({
          name: v.name,
          label: v.label || '',
          hint: v.hint || '',
        }));
        if (JSON.stringify(declared) !== JSON.stringify(orig)) {
          await setPipelineVariables(id, declared);
        }
      }
      show(contentChanged ? 'New version committed ✓' : 'Prompt updated!');
      navigate(plPrompt(id));
    } catch (err) {
      show(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  // Advisory dedup (doc §10): on create, warn when a live same-kind prompt
  // already carries this exact content — the user decides; never blocks.
  async function confirmNoDuplicate() {
    if (isEdit) return true;
    const dup = await checkDuplicate(content.trim(), isPipeline ? 'pipeline' : 'library');
    if (!dup) return true;
    return window.confirm(
      `"${dup.title}" already has identical content${dup.category ? ` (category: ${dup.category})` : ''}. Create a duplicate anyway?`,
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!title.trim() || !content.trim()) {
      show(isPipeline ? 'Registry name and user prompt template are required.' : 'Title and content are required.');
      return;
    }
    if (!(await confirmNoDuplicate())) return;
    if (isPipeline) {
      if (!systemPrompt.trim()) {
        show('System prompt is required for pipeline prompts.');
        return;
      }
      await handlePipelineSubmit();
      return;
    }
    if (visibility === 'team' && selectedTeamIds.length === 0) {
      show('Select at least one team for team-based visibility.');
      return;
    }
    if (!category.trim()) {
      show('Category is required.');
      return;
    }
    // Only reachable on EDIT of a legacy library row — creation is always
    // pipeline now.
    setSaving(true);
    try {
      const body = {
        title: title.trim(),
        content: content.trim(),
        description: description.trim(),
        category: category.trim(),
        tags: tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        visibility,
        teams: visibility === 'team' ? selectedTeamIds : [],
        variables,
        create_version: createVersion,
        version_note: versionNote.trim(),
      };
      if (
        loadedPrompt?.can_have_children !== false &&
        !(loadedPrompt?.children && loadedPrompt.children.length > 0)
      ) {
        body.parent_id = parentId || null;
      }
      await updatePrompt(id, body);
      show('Prompt updated!');
      navigate(plPrompt(id));
    } catch (err) {
      show(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleUpload(e) {
    if (!id || !e.target.files?.[0]) {
      show('Save the prompt first to add attachments.');
      return;
    }
    try {
      await uploadAttachment(id, e.target.files[0]);
      const p = await fetchPrompt(id);
      if (p) setAttachments(p.attachments || []);
      show('File uploaded!');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Upload failed');
    }
    e.target.value = '';
  }

  async function handleDeleteAtt(aid) {
    if (!id) return;
    await deleteAttachment(id, aid);
    const p = await fetchPrompt(id);
    if (p) setAttachments(p.attachments || []);
    show('Attachment deleted.');
  }

  if (loading) {
    return <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 48 }}>Loading…</p>;
  }

  // Creation is pipeline-manager-only (the nav action already hides it; this
  // guards direct URL hits) — freeform library creation is retired (12b).
  if (!isEdit && !canPipeline) {
    return <Navigate to={plCourses} replace />;
  }

  return (
    <>
      <Link to={isEdit && id ? plPrompt(id) : plHome} className="back-link">
        ← {isEdit ? 'Back to prompt' : 'Back to library'}
      </Link>
      <div className="page-header">
        <h1>{isEdit ? 'Edit prompt' : 'New prompt'}</h1>
      </div>

      <form className="page-card" onSubmit={(e) => void handleSubmit(e)}>
        {!isEdit && (
          <p className="var-tip" style={{ marginTop: 0 }}>
            ⚙ This creates a CAS pipeline prompt — generation uses it once an
            admin makes it a stage default or binds it to a scope. Saving goes
            through the approval workflow; admin saves deploy immediately.
          </p>
        )}
        {!isPipeline && loadedPrompt?.parent && (
          <div className="field">
            <label>Parent prompt</label>
            <p style={{ fontSize: '.9rem' }}>
              <Link to={plPrompt(loadedPrompt.parent.id)}>{loadedPrompt.parent.title}</Link>
            </p>
          </div>
        )}
        {isEdit && !isPipeline && loadedPrompt?.children && loadedPrompt.children.length > 0 && (
          <div className="detail-section" style={{ marginTop: 0, paddingTop: 0, border: 'none' }}>
            <div className="section-hdr">Follow-up prompts</div>
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              {loadedPrompt.children.map((c) => (
                <li key={c.id} style={{ marginBottom: 6 }}>
                  <Link to={plPrompt(c.id)}>{c.title}</Link>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="field">
          <label>
            {isPipeline ? 'Registry name' : 'Title'}
            {' '}<span className="required-mark" aria-hidden="true">*</span>
          </label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            readOnly={isPipeline && isEdit}
            placeholder={isPipeline ? 'unique_slug_generation' : undefined}
          />
          {isPipeline && isEdit && (
            <p className="var-tip" style={{ marginTop: 6 }}>
              The registry name is the row&apos;s identity — it cannot be renamed.
            </p>
          )}
        </div>
        {isPipeline && (
          <>
            <div className="inline-fields">
              <div className="field">
                <label>Component</label>
                <select
                  value={componentType}
                  onChange={(e) => setComponentType(e.target.value)}
                  disabled={isEdit && (!canPipeline || loadedPrompt?.pipeline?.is_default)}
                >
                  <option value="">— none —</option>
                  <option value="style">{L.style} (style)</option>
                  <option value="cdd">{L.cdd} (cdd)</option>
                  <option value="blueprint">{L.blueprint} (blueprint)</option>
                  <option value="generate">Lesson Generation (generate)</option>
                  <option value="quiz">Assessment (quiz)</option>
                </select>
              </div>
              <div className="field">
                <label>Variant (optional)</label>
                <input
                  value={variant}
                  onChange={(e) => setVariant(e.target.value)}
                  list="variantList"
                  placeholder="e.g. teacher, student, interactive (generate + interactive = the Component category)"
                  disabled={isEdit && (!canPipeline || loadedPrompt?.pipeline?.is_default)}
                />
                <datalist id="variantList">
                  <option value="teacher" />
                  <option value="student" />
                  <option value="interactive" />
                </datalist>
              </div>
            </div>
            {isEdit && loadedPrompt?.pipeline?.is_default && (
              <p className="var-tip" style={{ marginTop: -6 }}>
                This row is the component default — clear the default flag (on
                the detail page) before re-keying component/variant.
              </p>
            )}
            {!isEdit && isPlatformAdmin && (
              <div className="field">
                <label>Tenant (optional)</label>
                <select value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
                  <option value="">— Shared / global (all tenants) —</option>
                  {tenantOptions.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
                <p className="var-tip" style={{ marginTop: 6 }}>
                  Leave as shared/global only for a prompt genuinely meant for
                  every tenant. Pick a tenant to keep this prompt private to it.
                </p>
              </div>
            )}
            <div className="field">
              <label>System prompt <span className="required-mark" aria-hidden="true">*</span></label>
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={6}
                required
              />
            </div>
          </>
        )}
        <div className="field">
          <label>
            {isPipeline ? 'User prompt template' : 'Content'}
            {' '}<span className="required-mark" aria-hidden="true">*</span>
          </label>
          <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={10} required />
          {legacyVars.length > 0 && (
            <p className="var-tip" style={{ color: 'var(--warning, #b45309)' }}>
              ⚠ Legacy single-brace placeholder{legacyVars.length > 1 ? 's' : ''} detected:{' '}
              {legacyVars.map((v) => `{${v}}`).join(', ')}. Prompts use double braces — write{' '}
              {legacyVars.map((v) => `{{${v}}}`).join(', ')} or the variable
              {legacyVars.length > 1 ? 's' : ''} won&apos;t be detected or filled.
            </p>
          )}
        </div>
        <div className="field">
          <label>Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
        </div>
        {!isPipeline && (
        <div className="inline-fields">
          <div className="field">
            <label>Category <span className="required-mark" aria-hidden="true">*</span></label>
            <input value={category} onChange={(e) => setCategory(e.target.value)} list="catList" required />
            <datalist id="catList" />
          </div>
          <div className="field">
            <label>Tags (comma-separated)</label>
            <input value={tags} onChange={(e) => setTags(e.target.value)} />
          </div>
        </div>
        )}
        {!isPipeline && (
        <div className="vis-row">
          <div className="field">
            <label>Visibility</label>
            <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
              <option value="draft">Draft (admin only)</option>
              <option value="team">Team / Pod</option>
              <option value="global">Global (all users)</option>
            </select>
          </div>
          {visibility === 'team' && (
            <div className="field">
              <label>Teams</label>
              {teamOptions.length === 0 ? (
                <p className="var-tip" style={{ marginTop: 0 }}>
                  No teams defined yet.
                </p>
              ) : (
                <TeamMultiSelect options={teamOptions} value={selectedTeamIds} onChange={setSelectedTeamIds} />
              )}
            </div>
          )}
        </div>
        )}

        {isPipeline && (
          <div className="field">
            <label>Change note</label>
            <input
              value={changeNote}
              onChange={(e) => setChangeNote(e.target.value)}
              placeholder={isEdit ? 'Why this version?' : 'Initial commit.'}
            />
            {isEdit && (
              <p className="var-tip" style={{ marginTop: 6 }}>
                Changing the prompt text commits a new version
                {canPipeline
                  ? ' and deploys it immediately.'
                  : ' as a draft for admin approval.'}
              </p>
            )}
          </div>
        )}

        {isPipeline && isEdit && canPipeline && (
          <div className="field">
            <label>Declared variables (strict enforcement)</label>
            <div className="var-editor">
              {pipeNamesUnion.length === 0 ? (
                <p className="var-tip" style={{ marginTop: 0 }}>
                  No {'{{placeholders}}'} in the prompt text — nothing to declare.
                </p>
              ) : (
                pipeNamesUnion.map((name) => {
                  const d = pipeVarDefs[name] || { declared: false, label: '', hint: '' };
                  const stale = !pipeNames.includes(name);
                  return (
                    <div key={name} className="var-def-row">
                      <label className="var-code" style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}>
                        <input
                          type="checkbox"
                          checked={d.declared}
                          onChange={(e) =>
                            setPipeVarDefs((prev) => ({
                              ...prev,
                              [name]: { ...d, declared: e.target.checked },
                            }))
                          }
                        />{' '}
                        {`{{${name}}}`}
                      </label>
                      {stale && (
                        <span style={{ fontSize: '.72rem', color: 'var(--warning, #b45309)' }}>
                          ⚠ declared but no longer in the text — uncheck or re-add it
                        </span>
                      )}
                      {d.declared && (
                        <div className="var-def-inputs">
                          <input
                            placeholder="Label"
                            value={d.label}
                            onChange={(e) =>
                              setPipeVarDefs((prev) => ({
                                ...prev,
                                [name]: { ...d, label: e.target.value },
                              }))
                            }
                          />
                          <input
                            placeholder="Hint (e.g. which router supplies it)"
                            value={d.hint}
                            onChange={(e) =>
                              setPipeVarDefs((prev) => ({
                                ...prev,
                                [name]: { ...d, hint: e.target.value },
                              }))
                            }
                          />
                        </div>
                      )}
                    </div>
                  );
                })
              )}
              <p className="var-tip">
                ⚡ Declaring a variable turns on strict enforcement: a generation
                call that does not supply it fails with PROMPT_MISCONFIGURED
                instead of silently falling back to the built-in values.
                Undeclared placeholders stay lenient.
              </p>
            </div>
          </div>
        )}

        {!isPipeline && Object.keys(varDefs).length > 0 && (
          <div className="field">
            <label>Variables</label>
            <div className="var-editor">
              {Object.entries(varDefs).map(([name, d]) => (
                <div key={name} className="var-def-row">
                  <span className="var-code">{`{{${name}}}`}</span>
                  <div className="var-def-inputs">
                    <input
                      placeholder="Label"
                      value={d.label}
                      onChange={(e) =>
                        setVarDefs((prev) => ({
                          ...prev,
                          [name]: { ...prev[name], label: e.target.value },
                        }))
                      }
                    />
                    <input
                      placeholder="Hint"
                      value={d.hint}
                      onChange={(e) =>
                        setVarDefs((prev) => ({
                          ...prev,
                          [name]: { ...prev[name], hint: e.target.value },
                        }))
                      }
                    />
                  </div>
                </div>
              ))}
              <p className="var-tip">✏ Customise label and hint for each variable.</p>
            </div>
          </div>
        )}
        {!isPipeline && !Object.keys(varDefs).length && (
          <p className="var-tip">💡 Add {'{{variable_name}}'} in content and fields appear here.</p>
        )}

        {isEdit && !isPipeline && (
          <>
            <div className="field">
              <label>
                <input type="checkbox" checked={createVersion} onChange={(e) => setCreateVersion(e.target.checked)} />{' '}
                Save as new version
              </label>
            </div>
            {createVersion && (
              <div className="field">
                <label>Version note</label>
                <input value={versionNote} onChange={(e) => setVersionNote(e.target.value)} />
              </div>
            )}
            <div className="detail-section">
              <div className="section-hdr">Attachments</div>
              <div className="attach-list">
                {attachments.length === 0 ? (
                  <div style={{ fontSize: '.78rem', color: 'var(--muted)' }}>No attachments.</div>
                ) : (
                  attachments.map((a) => (
                    <div key={a.id} className="attach-item">
                      <span>📎</span>
                      <span className="attach-name">{a.original_name}</span>
                      <span className="attach-size">{(a.size / 1024).toFixed(1)} KB</span>
                      <button type="button" className="attach-del" onClick={() => void handleDeleteAtt(a.id)}>
                        🗑
                      </button>
                    </div>
                  ))
                )}
              </div>
              <div className="upload-row">
                <input type="file" onChange={(e) => void handleUpload(e)} />
              </div>
            </div>
          </>
        )}

        <div className="modal-footer" style={{ marginTop: 24 }}>
          <Link to={isEdit && id ? plPrompt(id) : plHome} className="btn btn-ghost">
            Cancel
          </Link>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving…' : isEdit ? 'Update prompt' : 'Create prompt'}
          </button>
        </div>
      </form>
    </>
  );
}
