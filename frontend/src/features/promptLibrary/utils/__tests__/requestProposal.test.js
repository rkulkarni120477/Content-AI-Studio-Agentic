import { describe, expect, it } from 'vitest';
import {
  buildRequestDescription,
  hasProposal,
  parseRequestDescription,
} from '../requestProposal';

describe('requestProposal', () => {
  it('round-trips rationale plus both blocks', () => {
    const stored = buildRequestDescription({
      rationale: 'Tone is too formal.',
      proposedSystem: 'You are a friendly ID expert.',
      proposedUser: 'Create a CDD for {{course_title}}.',
    });
    const parsed = parseRequestDescription(stored);
    expect(parsed.rationale).toBe('Tone is too formal.');
    expect(parsed.proposedSystem).toBe('You are a friendly ID expert.');
    expect(parsed.proposedUser).toBe('Create a CDD for {{course_title}}.');
    expect(hasProposal(stored)).toBe(true);
  });

  it('round-trips a single block (user template only)', () => {
    const stored = buildRequestDescription({
      rationale: 'Only the template changes.',
      proposedSystem: null,
      proposedUser: 'New template text.',
    });
    const parsed = parseRequestDescription(stored);
    expect(parsed.rationale).toBe('Only the template changes.');
    expect(parsed.proposedSystem).toBeNull();
    expect(parsed.proposedUser).toBe('New template text.');
  });

  it('handles a proposal with no rationale', () => {
    const stored = buildRequestDescription({
      rationale: '',
      proposedSystem: 'Sys.',
      proposedUser: null,
    });
    const parsed = parseRequestDescription(stored);
    expect(parsed.rationale).toBe('');
    expect(parsed.proposedSystem).toBe('Sys.');
  });

  it('treats plain descriptions as rationale only', () => {
    const parsed = parseRequestDescription('Please add Python prompts.');
    expect(parsed).toEqual({
      rationale: 'Please add Python prompts.',
      proposedSystem: null,
      proposedUser: null,
    });
    expect(hasProposal('Please add Python prompts.')).toBe(false);
    expect(hasProposal('')).toBe(false);
    expect(hasProposal(null)).toBe(false);
  });

  it('preserves multi-line prompt bodies', () => {
    const body = 'Line one.\n\nLine two with {{vars}}.\n- bullet';
    const parsed = parseRequestDescription(
      buildRequestDescription({ rationale: 'r', proposedSystem: body, proposedUser: null }),
    );
    expect(parsed.proposedSystem).toBe(body);
  });
});
