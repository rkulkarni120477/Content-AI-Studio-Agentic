import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  createPrompt,
  deleteAttachment,
  fetchPrompt,
  fetchRootPrompts,
  updatePrompt,
  uploadAttachment,
} from '../api/prompts';
import { fetchTeams } from '../api/teams';
import { useToast } from '../context/ToastContext';
import { canManagePrompts } from '../utils/permissions';
import { useAuth } from '../context/AuthContext';
import TeamMultiSelect from '../components/TeamMultiSelect';
import { extractVarNames, toLabel } from '../utils/prompt';
import { plHome, plPrompt, plPromptEdit } from '../paths';

export default function PromptFormPage() {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const initialParentId = searchParams.get('parentId') || '';
  const isEdit = Boolean(id);
  const navigate = useNavigate();
  const { show } = useToast();

  const [teamOptions, setTeamOptions] = useState([]);
  const [rootPrompts, setRootPrompts] = useState([]);
  const [parentId, setParentId] = useState(initialParentId);
  const [loadedPrompt, setLoadedPrompt] = useState(null);
  const [loading, setLoading] = useState(isEdit);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('');
  const [tags, setTags] = useState('');
  const [visibility, setVisibility] = useState('draft');
  const [selectedTeamIds, setSelectedTeamIds] = useState([]);
  const [createVersion, setCreateVersion] = useState(false);
  const [versionNote, setVersionNote] = useState('');
  const [varDefs, setVarDefs] = useState({});
  const [attachments, setAttachments] = useState([]);
  const [saving, setSaving] = useState(false);

  const { user } = useAuth();
  const canEdit = canManagePrompts(user);

  useEffect(() => {
    void fetchTeams().then(setTeamOptions);
    if (canEdit) void fetchRootPrompts().then(setRootPrompts);
  }, [canEdit]);

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

  async function handleSubmit(e) {
    e.preventDefault();
    if (!title.trim() || !content.trim()) {
      show('Title and content are required.');
      return;
    }
    if (visibility === 'team' && selectedTeamIds.length === 0) {
      show('Select at least one team for team-based visibility.');
      return;
    }
    setSaving(true);
    try {
      const body = {
        title: title.trim(),
        content: content.trim(),
        description: description.trim(),
        category: category.trim() || 'General',
        tags: tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        visibility,
        teams: visibility === 'team' ? selectedTeamIds : [],
        variables,
        create_version: isEdit ? createVersion : false,
        version_note: isEdit ? versionNote.trim() : '',
      };
      if (!isEdit) {
        body.parent_id = parentId || null;
      } else if (
        loadedPrompt?.can_have_children !== false &&
        !(loadedPrompt?.children && loadedPrompt.children.length > 0)
      ) {
        body.parent_id = parentId || null;
      }
      if (isEdit && id) {
        await updatePrompt(id, body);
        show('Prompt updated!');
        navigate(plPrompt(id));
      } else {
        const created = await createPrompt(body);
        show('Prompt saved!');
        navigate(plPromptEdit(created.id));
      }
    } catch (err) {
      show(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleUpload(e) {
    if (!id || !e.target.files?.[0]) {
      show('Save prompt first to add attachments');
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

  return (
    <>
      <Link to={isEdit && id ? plPrompt(id) : plHome} className="back-link">
        ← {isEdit ? 'Back to prompt' : 'Back to library'}
      </Link>
      <div className="page-header">
        <h1>{isEdit ? 'Edit prompt' : parentId ? 'New follow-up prompt' : 'New prompt'}</h1>
      </div>

      <form className="page-card" onSubmit={(e) => void handleSubmit(e)}>
        {loadedPrompt?.parent && (
          <div className="field">
            <label>Parent prompt</label>
            <p style={{ fontSize: '.9rem' }}>
              <Link to={plPrompt(loadedPrompt.parent.id)}>{loadedPrompt.parent.title}</Link>
            </p>
          </div>
        )}
        {!isEdit && canEdit && loadedPrompt?.can_have_children !== false && (
          <div className="field">
            <label>Parent prompt (optional)</label>
            <select value={parentId} onChange={(e) => setParentId(e.target.value)} disabled={Boolean(initialParentId)}>
              <option value="">— None (standalone prompt) —</option>
              {rootPrompts
                .filter((rp) => rp.id !== id)
                .map((rp) => (
                  <option key={rp.id} value={rp.id}>
                    {rp.title}
                  </option>
                ))}
            </select>
            <p className="var-tip" style={{ marginTop: 6 }}>
              Follow-up prompts belong to a parent and cannot have their own follow-ups.
            </p>
          </div>
        )}
        {isEdit && loadedPrompt?.children && loadedPrompt.children.length > 0 && (
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
          <label>Title *</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </div>
        <div className="field">
          <label>Content *</label>
          <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={10} required />
        </div>
        <div className="field">
          <label>Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
        </div>
        <div className="inline-fields">
          <div className="field">
            <label>Category</label>
            <input value={category} onChange={(e) => setCategory(e.target.value)} list="catList" />
            <datalist id="catList" />
          </div>
          <div className="field">
            <label>Tags (comma-separated)</label>
            <input value={tags} onChange={(e) => setTags(e.target.value)} />
          </div>
        </div>
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

        {Object.keys(varDefs).length > 0 && (
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
        {!Object.keys(varDefs).length && (
          <p className="var-tip">💡 Add {'{{variable_name}}'} in content and fields appear here.</p>
        )}

        {isEdit && (
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
