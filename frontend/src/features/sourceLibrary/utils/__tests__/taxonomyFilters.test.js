import { describe, expect, it } from 'vitest';
import {
  apiParamForTaxonomyKey,
  optionsKeyForTaxonomyKey,
  taxonomyFiltersFromUiConfig,
  toSourceLibraryApiFilters,
} from '../taxonomyFilters';

describe('taxonomyFiltersFromUiConfig', () => {
  it('returns configured taxonomy filters with labels and keys from config', () => {
    const uiConfig = {
      source_library: {
        taxonomy_filters: [
          { key: 'course_name', label: 'Course / Product', type: 'select' },
          { key: 'chapter', label: 'Chapter', type: 'select' },
          { key: 'module', label: 'Module / Section', type: 'select' },
          { key: 'learning_objective', label: 'Learning Objective', type: 'text' },
        ],
      },
    };
    const filters = taxonomyFiltersFromUiConfig(uiConfig);
    expect(filters.map((f) => f.key)).toEqual([
      'course_name', 'chapter', 'module', 'learning_objective',
    ]);
    expect(filters.map((f) => f.label)).toEqual([
      'Course / Product', 'Chapter', 'Module / Section', 'Learning Objective',
    ]);
  });

  it('defers AIM topic until a backend path exists', () => {
    const uiConfig = {
      source_library: {
        taxonomy_filters: [
          { key: 'block', label: 'Block', type: 'select' },
          { key: 'topic', label: 'Topic', type: 'text' },
        ],
      },
    };
    expect(taxonomyFiltersFromUiConfig(uiConfig).map((f) => f.key)).toEqual(['block']);
  });

  it('returns [] when taxonomy_filters are missing', () => {
    expect(taxonomyFiltersFromUiConfig(null)).toEqual([]);
    expect(taxonomyFiltersFromUiConfig({})).toEqual([]);
  });
});

describe('module → module_name mapping', () => {
  it('maps module to module_name for the API contract', () => {
    expect(apiParamForTaxonomyKey('module')).toBe('module_name');
    expect(apiParamForTaxonomyKey('course_name')).toBe('course_name');
  });

  it('sends module_name when the UI filter key is module', () => {
    const params = toSourceLibraryApiFilters({
      purpose: 'cdd',
      module: 'Section 3',
      search: 'guide',
    });
    expect(params).toEqual({
      purpose: 'cdd',
      module_name: 'Section 3',
      search: 'guide',
    });
    expect(params).not.toHaveProperty('module');
  });
});

describe('toSourceLibraryApiFilters', () => {
  it('forwards existing common filters unchanged', () => {
    expect(toSourceLibraryApiFilters({
      purpose: 'style',
      document_type: 'syllabus',
      status: 'processed',
      search: 'block 9',
    })).toEqual({
      purpose: 'style',
      document_type: 'syllabus',
      status: 'processed',
      search: 'block 9',
    });
  });

  it('omits empty values so the server applies the filter', () => {
    expect(toSourceLibraryApiFilters({ purpose: '', block: 'Block 9' })).toEqual({
      block: 'Block 9',
    });
  });

  it('does not send deferred topic', () => {
    expect(toSourceLibraryApiFilters({ topic: 'landing gear', block: 'Block 2' })).toEqual({
      block: 'Block 2',
    });
  });
});

describe('optionsKeyForTaxonomyKey', () => {
  it('maps UI keys onto source_filter_options response keys', () => {
    expect(optionsKeyForTaxonomyKey('block')).toBe('blocks');
    expect(optionsKeyForTaxonomyKey('day')).toBe('days');
    expect(optionsKeyForTaxonomyKey('module')).toBe('module');
    expect(optionsKeyForTaxonomyKey('course_name')).toBe('course_name');
  });
});
