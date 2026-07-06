// Course-grouped prompt view (Phase 11, requirements-doc §2/§6.2/§8).
//
// Courses as collapsible groups; expanding one shows the prompt every
// pipeline category resolves to for that course. The same default prompt
// appears under every course that resolves it while remaining ONE master
// row — display is per-course, storage is deduplicated (the doc's §6.2).
// Resolution is server-computed (GET /api/v1/prompts/by-course) with the
// exact semantics generation uses: scope lock (course → cluster → project
// → global) → component default → shipped file template.
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchPromptsByCourse } from '../api/flow';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { canManagePipelinePrompts } from '../utils/permissions';
import { componentCategoryLabel } from '../utils/prompt';
import { plPrompt } from '../paths';

const SOURCE_BADGES = {
  course_lock: { className: 'badge-global', text: '🔒 Course lock' },
  cluster_lock: { className: 'badge-global', text: '🔒 Cluster lock' },
  project_lock: { className: 'badge-global', text: '🔒 Project lock' },
  global_lock: { className: 'badge-global', text: '🔒 Global lock' },
  default: { className: 'badge-team', text: 'Default' },
  file: { className: 'badge-draft', text: 'File template' },
  builtin: { className: 'badge-draft', text: 'Built-in builder' },
};

function SlotRow({ slot, canOpen }) {
  const badge = SOURCE_BADGES[slot.source] || { className: 'badge-draft', text: slot.source };
  const p = slot.prompt;
  const label = p ? p.name + (p.variant ? ` (${p.variant})` : '') : null;
  return (
    <div className="list-row" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <span style={{ minWidth: 150, fontWeight: 600 }}>
        {componentCategoryLabel(slot.component, slot.variant)}
      </span>
      <span className={`badge ${badge.className}`}>{badge.text}</span>
      {p ? (
        canOpen ? (
          <Link to={plPrompt(p.id)}>{label}</Link>
        ) : (
          <span>{label}</span>
        )
      ) : (
        <span style={{ color: 'var(--muted)', fontSize: '.85rem' }}>
          {slot.source === 'builtin'
            ? 'No prompt row — the component builder generates this content.'
            : 'No prompt row — the shipped file template is used.'}
        </span>
      )}
      {p?.is_default && slot.source !== 'default' && (
        <span className="badge badge-team" title="This row is also the component default">
          ★ default row
        </span>
      )}
    </div>
  );
}

export default function CoursePromptsPage() {
  const { user } = useAuth();
  const { show } = useToast();
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState({}); // course_id -> bool
  const [selProject, setSelProject] = useState('');
  const canOpen = canManagePipelinePrompts(user);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchPromptsByCourse()
      .then((items) => {
        if (alive) setGroups(items);
      })
      .catch(() => show('Could not load the course-grouped view.'))
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const projects = useMemo(() => {
    const seen = new Map();
    for (const g of groups) {
      if (!seen.has(g.project_id)) seen.set(g.project_id, g.project_name || `Project ${g.project_id}`);
    }
    return [...seen.entries()];
  }, [groups]);

  const visible = selProject
    ? groups.filter((g) => String(g.project_id) === String(selProject))
    : groups;

  return (
    <div>
      <div className="page-card" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: '1.05rem' }}>Prompts by course</h2>
        <p style={{ margin: '6px 0 10px', fontSize: '.85rem', color: 'var(--muted)' }}>
          Every course with the prompt each category resolves to. A shared default shows
          under every course that uses it — it is still a single prompt; editing it once
          updates all of them. Course-specific overrides are the 🔒 scope locks (set them
          in the Flow view).
        </p>
        <div className="field" style={{ maxWidth: 320 }}>
          <label>Project</label>
          <select value={selProject} onChange={(e) => setSelProject(e.target.value)}>
            <option value="">All projects</option>
            {projects.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading && <p style={{ color: 'var(--muted)' }}>Loading…</p>}
      {!loading && visible.length === 0 && (
        <p style={{ color: 'var(--muted)' }}>No courses found.</p>
      )}

      {visible.map((g) => {
        const open = !!expanded[g.course_id];
        return (
          <div key={g.course_id} className="page-card" style={{ marginBottom: 10 }}>
            <button
              type="button"
              onClick={() => setExpanded((prev) => ({ ...prev, [g.course_id]: !open }))}
              aria-expanded={open}
              style={{
                display: 'flex',
                alignItems: 'baseline',
                gap: 10,
                width: '100%',
                background: 'none',
                border: 'none',
                padding: 0,
                cursor: 'pointer',
                textAlign: 'left',
                font: 'inherit',
                color: 'inherit',
              }}
            >
              <span style={{ fontWeight: 700 }}>{open ? '−' : '+'}</span>
              <span style={{ fontWeight: 700 }}>{g.course_name}</span>
              <span style={{ fontSize: '.8rem', color: 'var(--muted)' }}>
                {g.project_name}
                {g.cluster_name ? ` · ${g.cluster_name}` : ''}
              </span>
            </button>
            {open && (
              <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
                {g.prompts.map((slot) => (
                  <SlotRow
                    key={`${slot.component}:${slot.variant || ''}`}
                    slot={slot}
                    canOpen={canOpen}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
