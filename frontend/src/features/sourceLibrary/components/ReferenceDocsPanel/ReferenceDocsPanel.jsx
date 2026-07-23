import { useEffect, useMemo, useState } from 'react';
import sourceLibraryApi from '@features/sourceLibrary/services/sourceLibraryApi';

const PURPOSE_LABELS = {
  style: 'Style reference documents',
  cdd: 'CDD reference documents',
  blueprint: 'Blueprint reference documents',
  course_generation: 'Title generation reference documents',
  general_reference: 'General reference documents',
};

function cacheKey({ courseId, projectId, purpose }) {
  return `cas_dis_reference_docs:${courseId || projectId || 'global'}:${purpose || 'all'}`;
}

function readCache(key) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || 'null');
    return Array.isArray(parsed?.documents) ? parsed.documents : [];
  } catch {
    return [];
  }
}

function writeCache(key, documents) {
  try {
    window.localStorage.setItem(key, JSON.stringify({ documents, cached_at: new Date().toISOString() }));
  } catch {
    // Ignore storage quota/privacy mode issues.
  }
}

export default function ReferenceDocsPanel({
  purpose = '',
  courseId = '',
  projectId = '',
  title = '',
  compact = false,
}) {
  const key = useMemo(() => cacheKey({ courseId, projectId, purpose }), [courseId, projectId, purpose]);
  const [docs, setDocs] = useState(() => readCache(key));
  const [loading, setLoading] = useState(false);
  const [warning, setWarning] = useState('');
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const cached = readCache(key);
      if (cached.length && !cancelled) setDocs(cached);
      setLoading(true);
      setWarning('');
      try {
        // No status filter: DIS `status` is a review axis (approved_candidate /
        // needs_review), not a processing-complete flag. Filtering by 'processed'
        // matched nothing. All source-index docs are already processed.
        const params = { purpose };
        if (courseId) params.course_id = courseId;
        else if (projectId) params.project_id = projectId;
        const res = await sourceLibraryApi.listDocuments(params);
        const list = res.documents || res.sources || [];
        if (!cancelled) {
          setDocs(list);
          writeCache(key, list);
        }
      } catch (err) {
        if (!cancelled) {
          setWarning('Showing last loaded document list. DIS is not reachable right now.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [key, purpose, courseId, projectId]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return docs;
    return docs.filter((d) => [d.title, d.source_file_name, d.document_type, d.purpose, d.document_id, d.job_id]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(q));
  }, [docs, query]);

  const visibleDocs = expanded ? filtered : filtered.slice(0, compact ? 4 : 8);

  return (
    <section style={{ border: '1px solid #e2e8f0', borderRadius: 14, padding: 14, background: '#fff', margin: '14px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 10 }}>
        <div>
          <div style={{ fontWeight: 800, color: '#0f172a' }}>📎 {title || PURPOSE_LABELS[purpose] || 'Reference documents'}</div>
          <div style={{ color: '#64748b', fontSize: 13 }}>
            {loading ? 'Checking Source Library…' : `${filtered.length} processed document(s) available`}
          </div>
        </div>
        {docs.length > 8 && (
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search documents"
            style={{ minWidth: 220, padding: '8px 10px', border: '1px solid #cbd5e1', borderRadius: 10 }}
          />
        )}
      </div>
      {warning && <div style={{ color: '#92400e', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: 10, marginBottom: 10 }}>{warning}</div>}
      {visibleDocs.length ? (
        <div style={{ display: 'grid', gap: 8 }}>
          {visibleDocs.map((doc) => (
            <div key={doc.document_id || doc.job_id || doc.source_file_name} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto auto', gap: 10, alignItems: 'center', border: '1px solid #eef2f7', borderRadius: 10, padding: '8px 10px' }}>
              <div style={{ minWidth: 0 }}>
                <strong style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.title || doc.source_file_name}</strong>
                <span style={{ color: '#64748b', fontSize: 12 }}>{doc.source_file_name || doc.document_id || doc.job_id}</span>
              </div>
              <span style={{ color: '#334155', fontSize: 12 }}>{doc.document_type || 'document'}</span>
              <span style={{ color: '#059669', fontSize: 12, fontWeight: 700 }}>{doc.status || 'processed'}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: '#64748b', padding: 10 }}>No processed reference documents found for this step yet.</div>
      )}
      {filtered.length > visibleDocs.length && (
        <button type="button" onClick={() => setExpanded(true)} style={{ marginTop: 10, border: '1px solid #cbd5e1', borderRadius: 8, background: '#fff', padding: '6px 10px', cursor: 'pointer' }}>
          Show all {filtered.length} documents
        </button>
      )}
      {expanded && filtered.length > (compact ? 4 : 8) && (
        <button type="button" onClick={() => setExpanded(false)} style={{ marginTop: 10, marginLeft: 8, border: '1px solid #cbd5e1', borderRadius: 8, background: '#fff', padding: '6px 10px', cursor: 'pointer' }}>
          Show less
        </button>
      )}
    </section>
  );
}
