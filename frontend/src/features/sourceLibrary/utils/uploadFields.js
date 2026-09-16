/**
 * Source Library upload metadata field helpers (Phase 5).
 *
 * Upload fields are defined in DIS retrieval.source_ui.upload and exposed via
 * uiConfig.upload_metadata (resolved against metadata_schemas). Separate from
 * taxonomy_filters and Field Registry.
 */

export function uploadMetadataFieldsFromUiConfig(uiConfig) {
  const raw = uiConfig?.upload_metadata?.fields;
  if (!Array.isArray(raw)) return [];
  return [...raw].sort((a, b) => {
    const ao = Number(a?.order ?? 0);
    const bo = Number(b?.order ?? 0);
    if (ao !== bo) return ao - bo;
    return String(a?.key || '').localeCompare(String(b?.key || ''));
  });
}

export function documentTypeFromUiConfig(uiConfig) {
  const dt = uiConfig?.upload_metadata?.document_type;
  if (dt && typeof dt === 'object') {
    return {
      key: dt.key || 'document_type',
      label: dt.label || 'Document Type',
      control: String(dt.control || 'text').toLowerCase() === 'select' && Array.isArray(dt.options) && dt.options.length
        ? 'select'
        : 'text',
      options: Array.isArray(dt.options) ? dt.options : [],
      placeholder: dt.placeholder || 'Optional, auto-detect if blank',
    };
  }
  return {
    key: 'document_type',
    label: 'Document Type',
    control: 'text',
    options: [],
    placeholder: 'Optional, auto-detect if blank',
  };
}

export function uploadControlType(field) {
  const control = String(field?.control || 'text').toLowerCase();
  if (control === 'select' && Array.isArray(field?.options) && field.options.length) return 'select';
  if (control === 'textarea') return 'textarea';
  return 'text';
}

export function isStructuralUploadField(key) {
  return key === 'purpose' || key === 'document_type';
}
