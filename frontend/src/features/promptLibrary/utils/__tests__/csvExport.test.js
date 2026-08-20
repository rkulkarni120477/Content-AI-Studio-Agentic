import { describe, expect, it } from 'vitest';
import { promptsToCsv } from '../csvExport';

const basePrompt = {
  id: 1,
  title: 'Simple',
  content: 'Body text',
  description: 'desc',
  category: 'General',
  visibility: 'global',
  created_by: 'alice',
  created_at: '2026-07-05',
  updated_at: '2026-07-05',
};

describe('promptsToCsv', () => {
  it('emits a header row plus one row per prompt, CRLF-joined', () => {
    const csv = promptsToCsv([basePrompt]);
    const lines = csv.split('\r\n');
    expect(lines).toHaveLength(2);
    expect(lines[0].startsWith('id,parent_id,parent_title,title,prompt')).toBe(true);
    expect(lines[1]).toContain('Simple');
  });

  it('escapes commas, quotes, and newlines per RFC 4180', () => {
    const csv = promptsToCsv([
      { ...basePrompt, title: 'Has, comma', description: 'say "hi"\nnewline' },
    ]);
    expect(csv).toContain('"Has, comma"');
    expect(csv).toContain('"say ""hi""\nnewline"');
  });

  it('joins teams, tags, and variable names with semicolons', () => {
    const csv = promptsToCsv([
      {
        ...basePrompt,
        teams: ['P1', 'P2'],
        tags: ['a', 'b'],
        variables: [{ name: 'x' }, { name: 'y' }],
      },
    ]);
    expect(csv).toContain('P1; P2');
    expect(csv).toContain('a; b');
    expect(csv).toContain('x; y');
  });

  it('defaults review stats and version count for sparse rows', () => {
    const line = promptsToCsv([basePrompt]).split('\r\n')[1];
    expect(line.endsWith('0,0,1')).toBe(true);
  });
});

// The export now runs off the rows the list already holds rather than a second
// fetch, so every column it writes has to survive in the browse-list
// projection — which deliberately omits `versions`, `attachments` and `parent`.
describe('promptsToCsv over a browse-list row', () => {
  const listRow = {
    id: 4,
    parent_id: null,
    title: 'CDD Builder',
    content: 'Write a CDD for {{course}}.',
    description: 'desc',
    category: '',
    visibility: 'global',
    teams: [],
    tags: ['cdd', 'cas'],
    variables: [{ name: 'course', label: 'Course', hint: '' }],
    created_by: 'admin',
    created_at: '2026-08-01',
    updated_at: '2026-08-02',
    last_used_at: null,
    prompt_kind: 'pipeline',
    archived: false,
    can_have_children: true,
    pipeline: {
      name: 'cdd_builder', component_type: 'cdd', variant: null,
      is_default: true, active_version: 'v2', workflow_state: 'active',
    },
    _review_stats: { count: 2, avg: 4.5 },
    _version_count: 3,
    _child_count: 0,
  };

  it('writes every column from the list projection alone', () => {
    const [header, row] = promptsToCsv([listRow]).split('\r\n');
    const cells = row.split(',');
    const at = (name) => cells[header.split(',').indexOf(name)];
    expect(at('id')).toBe('4');
    expect(at('title')).toBe('CDD Builder');
    expect(at('prompt')).toBe('Write a CDD for {{course}}.');
    expect(at('category')).toBe('CDD');            // derived from the pipeline block
    expect(at('tags')).toBe('cdd; cas');
    expect(at('variable_names')).toBe('course');
    expect(at('created_by')).toBe('admin');
    expect(at('review_count')).toBe('2');
    expect(at('review_avg')).toBe('4.5');
    // Falls back to _version_count now that `versions` is not in the payload.
    expect(at('version_count')).toBe('3');
    // parent_title has always been empty here: the list is fetched roots_only.
    expect(at('parent_title')).toBe('');
  });
});
