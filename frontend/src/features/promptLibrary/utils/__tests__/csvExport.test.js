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
