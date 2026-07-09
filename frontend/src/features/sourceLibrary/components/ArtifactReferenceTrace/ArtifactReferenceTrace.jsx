import { useMemo, useState } from 'react';

function parseMaybeJson(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return {}; }
}

function normalizeRefs(raw) {
  if (!raw) return [];
  const list = Array.isArray(raw) ? raw : [raw];
  return list
    .map((item, index) => {
      if (!item) return null;
      if (typeof item === 'string') return { id: item, title: item, source_file_name: item, index };
      const title = item.source_file_name || item.title || item.document_title || item.filename || item.name || item.content_unit_id || item.document_id || item.job_id || `Reference ${index + 1}`;
      return { ...item, id: item.document_id || item.job_id || item.content_unit_id || title, title, index };
    })
    .filter(Boolean);
}

export function extractArtifactReferences(...params) {
  for (const raw of params) {
    const parsed = parseMaybeJson(raw);
    const refs = normalizeRefs(parsed.dis_source_units || parsed.source_units || parsed.source_documents_used || parsed.source_document_ids || parsed.reference_documents);
    if (refs.length) return refs;
  }
  return [];
}

export default function ArtifactReferenceTrace({ title = 'Reference documents used for this item', generationParams, fallbackParams }) {
  const refs = useMemo(() => extractArtifactReferences(generationParams, fallbackParams), [generationParams, fallbackParams]);
  const [open, setOpen] = useState(false);

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 12, padding: 10, margin: '10px 0', background: '#fff' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{ width: '100%', border: 0, background: 'transparent', display: 'flex', justifyContent: 'space-between', cursor: 'pointer', fontWeight: 800, color: '#334155', padding: 0 }}
      >
        <span>📎 {title}{refs.length ? ` (${refs.length})` : ''}</span>
        <span>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        refs.length ? (
          <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
            {refs.map((ref) => (
              <div key={`${ref.id}-${ref.index}`} style={{ border: '1px solid #eef2f7', borderRadius: 10, padding: '8px 10px', background: '#f8fafc' }}>
                <strong style={{ display: 'block', color: '#0f172a' }}>{ref.title}</strong>
                <div style={{ color: '#64748b', fontSize: 12 }}>
                  {[ref.document_type || ref.unit_type, ref.purpose, ref.score != null ? `score ${ref.score}` : '', ref.job_id ? `job ${ref.job_id}` : ''].filter(Boolean).join(' · ')}
                </div>
              </div>
            ))}
          </div>
        ) : <div style={{ marginTop: 8, color: '#64748b' }}>No reference documents were recorded for this generated item.</div>
      )}
    </div>
  );
}
