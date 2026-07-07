// Flow-organized console view (Phase 7b, Decision 6 / locked "Console shape").
//
// Presents prompts by the workflow hierarchy: pick Project → Course, see what
// each generation phase (Style / CDD / Blueprint / Generate / Quiz) actually
// resolves — scope lock, component default, or file template — and bind an
// existing pipeline prompt to a scope by reference (a PromptFixing row, never
// a copy). Phase-appropriateness and the authors-only-approved rule are
// enforced server-side; this page surfaces those errors verbatim.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  bindFixing,
  listComponentPrompts,
  listProjects,
  listProjectCourses,
  resolveFixing,
  unbindFixing,
} from '../api/flow';
import { useToast } from '../context/ToastContext';
import { plPrompt } from '../paths';

const PHASES = [
  {
    key: 'style',
    label: 'Style',
    desc: 'Style extraction — reference documents → tone/voice profile.',
  },
  {
    key: 'cdd',
    label: 'CDD',
    desc: 'Course structural planning.',
  },
  {
    key: 'blueprint',
    label: 'Blueprint',
    desc: 'Module detailing — teacher/student variants resolve at generation time.',
  },
  {
    key: 'generate',
    label: 'Lesson Generation',
    desc: 'Lesson authoring (key: generate; variant=interactive serves the Component category).',
  },
  {
    key: 'quiz',
    label: 'Assessment',
    desc: 'Quiz / assessment authoring (key: quiz).',
  },
];

const SCOPE_LABELS = { global: 'Global', project: 'Project', cluster: 'Cluster', course: 'Course' };

function promptLabel(p) {
  if (!p) return null;
  return `${p.name}${p.variant ? ` (${p.variant})` : ''}${p.is_default ? ' ★default' : ''}`;
}

export default function PipelineFlowPage() {
  const { show } = useToast();

  const [projects, setProjects] = useState([]);
  const [courses, setCourses] = useState([]);
  const [selProject, setSelProject] = useState('');
  const [selCourse, setSelCourse] = useState('');

  const [pools, setPools] = useState({});      // phase key -> PromptListItem[]
  const [fixings, setFixings] = useState({});  // phase key -> PromptFixingRead | null
  const [choice, setChoice] = useState({});    // phase key -> { promptId, scope }
  const [busy, setBusy] = useState({});        // phase key -> bool

  const course = courses.find((c) => String(c.id) === String(selCourse)) || null;
  const ctx = useMemo(
    () => ({
      projectId: selProject ? Number(selProject) : null,
      clusterId: course?.cluster_id ?? null,
      courseId: course ? course.id : null,
    }),
    [selProject, course],
  );

  useEffect(() => {
    void listProjects().then(setProjects).catch(() => show('Could not load projects.'));
    for (const ph of PHASES) {
      void listComponentPrompts(ph.key)
        .then((items) => setPools((prev) => ({ ...prev, [ph.key]: items })))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setSelCourse('');
    setCourses([]);
    if (!selProject) return;
    void listProjectCourses(selProject)
      .then(setCourses)
      .catch(() => show('Could not load courses.'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selProject]);

  const refreshFixing = useCallback(
    async (phaseKey) => {
      try {
        const f = await resolveFixing({ component: phaseKey, ...ctx });
        setFixings((prev) => ({ ...prev, [phaseKey]: f }));
      } catch {
        setFixings((prev) => ({ ...prev, [phaseKey]: null }));
      }
    },
    [ctx],
  );

  useEffect(() => {
    for (const ph of PHASES) void refreshFixing(ph.key);
  }, [refreshFixing]);

  async function handleBind(phaseKey) {
    const sel = choice[phaseKey] || {};
    if (!sel.promptId || !sel.scope) {
      show('Pick a prompt and a scope first.');
      return;
    }
    setBusy((prev) => ({ ...prev, [phaseKey]: true }));
    try {
      await bindFixing({
        component: phaseKey,
        scopeLevel: sel.scope,
        projectId: sel.scope === 'project' ? ctx.projectId : null,
        clusterId: sel.scope === 'cluster' ? ctx.clusterId : null,
        courseId: sel.scope === 'course' ? ctx.courseId : null,
        promptId: Number(sel.promptId),
      });
      await refreshFixing(phaseKey);
      show(`Bound to the ${SCOPE_LABELS[sel.scope].toLowerCase()} scope ✓`);
    } catch (err) {
      show(err instanceof Error ? err.message : 'Bind failed');
    } finally {
      setBusy((prev) => ({ ...prev, [phaseKey]: false }));
    }
  }

  async function handleUnbind(phaseKey) {
    const f = fixings[phaseKey];
    if (!f) return;
    setBusy((prev) => ({ ...prev, [phaseKey]: true }));
    try {
      await unbindFixing({
        component: phaseKey,
        scopeLevel: f.scope_level,
        projectId: f.project_id,
        clusterId: f.cluster_id,
        courseId: f.course_id,
      });
      await refreshFixing(phaseKey);
      show('Scope lock removed — back to default resolution.');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Unbind failed');
    } finally {
      setBusy((prev) => ({ ...prev, [phaseKey]: false }));
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Pipeline flow</h1>
          <p className="view-meta">
            What each generation phase resolves for a given course — and where
            to bind a different approved prompt by reference. Locks resolve
            most-specific-first: course → cluster → project → global.
          </p>
        </div>
      </div>

      <div className="page-card" style={{ marginBottom: 16 }}>
        <div className="inline-fields">
          <div className="field">
            <label>Project</label>
            <select value={selProject} onChange={(e) => setSelProject(e.target.value)}>
              <option value="">— none (global view) —</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Course</label>
            <select value={selCourse} onChange={(e) => setSelCourse(e.target.value)} disabled={!selProject}>
              <option value="">— none (project scope) —</option>
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {PHASES.map((phase) => {
        const pool = pools[phase.key] || [];
        const fixing = fixings[phase.key] || null;
        const bound = fixing ? pool.find((p) => p.id === fixing.prompt_id) : null;
        const baseDefault = pool.find((p) => p.is_default && !p.variant) || null;
        const variantDefaults = pool.filter((p) => p.is_default && p.variant);
        const sel = choice[phase.key] || { promptId: '', scope: ctx.courseId ? 'course' : 'global' };

        return (
          <div key={phase.key} className="page-card" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <h2 style={{ margin: 0, fontSize: '1.05rem' }}>{phase.label}</h2>
              <span style={{ fontSize: '.8rem', color: 'var(--muted)' }}>{phase.desc}</span>
            </div>

            <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {fixing ? (
                <>
                  <span className="badge badge-global">
                    🔒 {SCOPE_LABELS[fixing.scope_level] || fixing.scope_level} lock
                  </span>
                  <span>
                    {bound ? (
                      <Link to={plPrompt(fixing.prompt_id)}>{promptLabel(bound)}</Link>
                    ) : (
                      `prompt #${fixing.prompt_id}`
                    )}
                  </span>
                  <span style={{ fontSize: '.75rem', color: 'var(--muted)' }}>
                    by {fixing.fixed_by || '—'}
                  </span>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    style={{ padding: '2px 10px', fontSize: '.75rem' }}
                    disabled={!!busy[phase.key]}
                    onClick={() => void handleUnbind(phase.key)}
                  >
                    Unbind
                  </button>
                </>
              ) : baseDefault ? (
                <>
                  <span className="badge badge-team">Component default</span>
                  <Link to={plPrompt(baseDefault.id)}>{promptLabel(baseDefault)}</Link>
                </>
              ) : (
                <span className="badge badge-draft">File template (no DB default)</span>
              )}
              {variantDefaults.length > 0 && (
                <span style={{ fontSize: '.75rem', color: 'var(--muted)' }}>
                  variant defaults:{' '}
                  {variantDefaults.map((v, i) => (
                    <span key={v.id}>
                      {i > 0 && ', '}
                      <Link to={plPrompt(v.id)}>{v.variant}</Link>
                    </span>
                  ))}
                </span>
              )}
            </div>

            <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div className="field" style={{ margin: 0, minWidth: 260 }}>
                <label style={{ fontSize: '.72rem' }}>Bind a prompt (by reference)</label>
                <select
                  value={sel.promptId}
                  onChange={(e) =>
                    setChoice((prev) => ({ ...prev, [phase.key]: { ...sel, promptId: e.target.value } }))
                  }
                >
                  <option value="">— choose a {phase.key} prompt —</option>
                  {pool.map((p) => (
                    <option key={p.id} value={p.id}>
                      {promptLabel(p)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field" style={{ margin: 0 }}>
                <label style={{ fontSize: '.72rem' }}>Scope</label>
                <select
                  value={sel.scope}
                  onChange={(e) =>
                    setChoice((prev) => ({ ...prev, [phase.key]: { ...sel, scope: e.target.value } }))
                  }
                >
                  <option value="global">Global</option>
                  <option value="project" disabled={!ctx.projectId}>
                    Project
                  </option>
                  <option value="cluster" disabled={!ctx.clusterId}>
                    Cluster
                  </option>
                  <option value="course" disabled={!ctx.courseId}>
                    Course
                  </option>
                </select>
              </div>
              <button
                type="button"
                className="btn btn-primary"
                style={{ padding: '6px 14px' }}
                disabled={!!busy[phase.key]}
                onClick={() => void handleBind(phase.key)}
              >
                Bind
              </button>
            </div>

          </div>
        );
      })}

      <p className="var-tip">
        Binding writes a scope lock (PromptFixing) — the prompt is referenced,
        not copied, so later edits to it propagate. Non-admins may bind only
        prompts whose active version is approved; only matching-component
        prompts are ever accepted.
      </p>
    </>
  );
}
