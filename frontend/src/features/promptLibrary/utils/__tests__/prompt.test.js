import { describe, expect, it } from 'vitest';
import {
  componentCategoryLabel,
  extractVarNames,
  fillPromptContent,
  findLegacyVarNames,
  pipelineStageLabel,
  starsDisplay,
  toLabel,
  visibilityLabel,
} from '../prompt';

describe('extractVarNames', () => {
  it('finds {{double}} placeholders, deduped, in appearance order', () => {
    expect(extractVarNames('Hi {{name}}, {{name}} of {{course_name}}')).toEqual([
      'name',
      'course_name',
    ]);
  });

  it('ignores {single} braces and non-identifier tokens', () => {
    expect(extractVarNames('legacy {x} and {"json": 1} and {{ spaced }}')).toEqual([]);
  });

  it('returns empty for empty content', () => {
    expect(extractVarNames('')).toEqual([]);
  });
});

describe('findLegacyVarNames', () => {
  it('detects {single}-brace identifiers', () => {
    expect(findLegacyVarNames('Use {style_context} here')).toEqual(['style_context']);
  });

  it('does not flag {{double}} placeholders', () => {
    expect(findLegacyVarNames('Use {{style_context}} here')).toEqual([]);
  });

  it('detects adjacent {x}{y} pairs (regression: the consumed-neighbor bug)', () => {
    expect(findLegacyVarNames('{x}{y}')).toEqual(['x', 'y']);
  });

  it('treats a brace-neighbored token as part of a double placeholder', () => {
    // "{{x}" and "{x}}" are malformed doubles, not legacy singles — warning on
    // them would tell the author to write "{{{x}}}", which is worse.
    expect(findLegacyVarNames('{{x} and {y}}')).toEqual([]);
  });

  it('ignores JSON-example braces (non-identifier content)', () => {
    expect(findLegacyVarNames('{"key": "value"} and {a b}')).toEqual([]);
  });

  it('mixed content flags only the legacy tokens', () => {
    expect(findLegacyVarNames('{{ok}} but {legacy} and {also_legacy}')).toEqual([
      'legacy',
      'also_legacy',
    ]);
  });
});

describe('fillPromptContent', () => {
  const vars = [{ name: 'a' }, { name: 'b' }];

  it('replaces every occurrence of each declared variable', () => {
    expect(fillPromptContent('{{a}}+{{a}}={{b}}', vars, { a: '1', b: '2' })).toBe('1+1=2');
  });

  it('fills missing values as empty string', () => {
    expect(fillPromptContent('[{{a}}][{{b}}]', vars, { a: 'x' })).toBe('[x][]');
  });

  it('leaves undeclared placeholders untouched', () => {
    expect(fillPromptContent('{{a}} {{other}}', vars, { a: 'x' })).toBe('x {{other}}');
  });
});

describe('toLabel', () => {
  it('title-cases snake_case names', () => {
    expect(toLabel('course_name')).toBe('Course Name');
    expect(toLabel('x')).toBe('X');
  });
});

describe('starsDisplay', () => {
  it.each([
    [0, '☆☆☆☆☆'],
    [3, '★★★☆☆'],
    [3.5, '★★★½☆'],
    [4.4, '★★★★☆'],
    [5, '★★★★★'],
  ])('renders %s as %s', (avg, expected) => {
    expect(starsDisplay(avg)).toBe(expected);
  });
});

describe('visibilityLabel', () => {
  it('maps host visibilities', () => {
    expect(visibilityLabel({ visibility: 'global' })).toEqual({
      className: 'badge-global',
      text: 'Global',
    });
    expect(visibilityLabel({})).toEqual({ className: 'badge-draft', text: 'Draft' });
  });

  it('lists team names for team visibility', () => {
    expect(visibilityLabel({ visibility: 'team', teams: ['P1', 'P2'] }).text).toBe(
      'Team: P1, P2',
    );
    expect(visibilityLabel({ visibility: 'team' }).text).toBe('Team');
  });

  it('maps the legacy standalone-app labels', () => {
    expect(visibilityLabel({ visibility: 'Public' }).text).toBe('Global');
    expect(visibilityLabel({ visibility: 'Private' }).text).toBe('Draft');
  });

  it('passes unknown values through as draft-styled text', () => {
    expect(visibilityLabel({ visibility: 'weird' })).toEqual({
      className: 'badge-draft',
      text: 'weird',
    });
  });
});

describe('pipelineStageLabel', () => {
  it('returns empty for library rows and null-ish input', () => {
    expect(pipelineStageLabel({ prompt_kind: 'library', category: 'Healthcare' })).toBe('');
    expect(pipelineStageLabel(undefined)).toBe('');
  });

  it('maps components to the requirements-doc category names', () => {
    expect(pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'style' } })).toBe('Style');
    expect(pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'cdd' } })).toBe('CDD');
    expect(pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'generate' } })).toBe(
      'Lesson Generation',
    );
    expect(pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'quiz' } })).toBe('Assessment');
  });

  it('maps (generate, interactive) to Component and treats lesson as the NULL variant', () => {
    expect(
      pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'generate', variant: 'interactive' } }),
    ).toBe('Component');
    expect(
      pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'generate', variant: 'lesson' } }),
    ).toBe('Lesson Generation');
  });

  it('appends the variant when present', () => {
    expect(
      pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'blueprint', variant: 'teacher' } }),
    ).toBe('Blueprint / teacher');
  });

  it('falls back to Pipeline for component-less rows and title-cases unknown components', () => {
    expect(pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: {} })).toBe('Pipeline');
    expect(pipelineStageLabel({ prompt_kind: 'pipeline' })).toBe('Pipeline');
    expect(pipelineStageLabel({ prompt_kind: 'pipeline', pipeline: { component_type: 'course_scaffold' } })).toBe(
      'Course Scaffold',
    );
  });
});

describe('componentCategoryLabel', () => {
  it('is the canonical key→category mapping used by every surface', () => {
    expect(componentCategoryLabel('quiz')).toBe('Assessment');
    expect(componentCategoryLabel('generate', 'interactive')).toBe('Component');
    expect(componentCategoryLabel('blueprint', 'student')).toBe('Blueprint / student');
    expect(componentCategoryLabel(null, 'legacy')).toBe('Pipeline / legacy');
    expect(componentCategoryLabel('')).toBe('Pipeline');
  });
});
