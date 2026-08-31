import { describe, expect, it } from 'vitest';
import {
  documentTypeFromUiConfig,
  isStructuralUploadField,
  uploadMetadataFieldsFromUiConfig,
} from '../uploadFields';

describe('uploadMetadataFieldsFromUiConfig', () => {
  it('returns configured upload fields in deterministic order', () => {
    const uiConfig = {
      upload_metadata: {
        fields: [
          { key: 'module_name', label: 'Module', control: 'text', order: 2 },
          { key: 'chapter', label: 'Chapter', control: 'text', order: 1 },
        ],
      },
    };
    expect(uploadMetadataFieldsFromUiConfig(uiConfig).map((f) => f.key)).toEqual([
      'chapter',
      'module_name',
    ]);
  });

  it('returns [] when upload configuration is absent', () => {
    expect(uploadMetadataFieldsFromUiConfig(null)).toEqual([]);
    expect(uploadMetadataFieldsFromUiConfig({})).toEqual([]);
    expect(uploadMetadataFieldsFromUiConfig({ source_library: {} })).toEqual([]);
  });

  it('does not include system-derived keys when backend omits them', () => {
    const uiConfig = {
      upload_metadata: {
        fields: [{ key: 'chapter', label: 'Chapter', control: 'text' }],
      },
    };
    const keys = uploadMetadataFieldsFromUiConfig(uiConfig).map((f) => f.key);
    expect(keys).not.toContain('file_sha256');
    expect(keys).toEqual(['chapter']);
  });
});

describe('documentTypeFromUiConfig', () => {
  it('uses select mode when schema-controlled values are provided', () => {
    const uiConfig = {
      upload_metadata: {
        document_type: {
          key: 'document_type',
          label: 'Document Type',
          control: 'select',
          options: ['syllabus', 'quiz_exam'],
        },
      },
    };
    const dt = documentTypeFromUiConfig(uiConfig);
    expect(dt.control).toBe('select');
    expect(dt.options).toEqual(['syllabus', 'quiz_exam']);
  });

  it('falls back to free-text when no controlled values exist', () => {
    const uiConfig = {
      upload_metadata: {
        document_type: {
          key: 'document_type',
          label: 'Document Type',
          control: 'text',
          placeholder: 'Optional, auto-detect if blank',
        },
      },
    };
    const dt = documentTypeFromUiConfig(uiConfig);
    expect(dt.control).toBe('text');
    expect(dt.placeholder).toBe('Optional, auto-detect if blank');
  });

  it('preserves legacy fallback when upload_metadata is missing', () => {
    const dt = documentTypeFromUiConfig({});
    expect(dt.control).toBe('text');
    expect(dt.key).toBe('document_type');
  });
});

describe('isStructuralUploadField', () => {
  it('treats purpose and document_type as structural', () => {
    expect(isStructuralUploadField('purpose')).toBe(true);
    expect(isStructuralUploadField('document_type')).toBe(true);
    expect(isStructuralUploadField('chapter')).toBe(false);
  });
});
