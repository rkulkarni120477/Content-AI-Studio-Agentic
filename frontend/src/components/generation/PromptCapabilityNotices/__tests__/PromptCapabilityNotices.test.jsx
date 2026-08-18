// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import PromptCapabilityNotices from '../PromptCapabilityNotices';

afterEach(cleanup);

const notice = (severity, message) => ({ severity, message });

const base = {
  assessed: true,
  has_findings: true,
  would_refuse_single_call: false,
  has_source_context_slot: true,
  notices: [notice('warning', 'Requests 4 day-table column(s) not emitted under that name.')],
};

describe('PromptCapabilityNotices', () => {
  // Silence is only correct when it means "nothing to say". These three cases mean
  // different things and none of them may render a reassuring message.
  it('renders nothing when capability is absent (not applicable)', () => {
    const { container } = render(<PromptCapabilityNotices capability={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when nothing was assessed', () => {
    const { container } = render(
      <PromptCapabilityNotices capability={{ assessed: false, notices: [] }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when the prompt and the pipeline agree', () => {
    const { container } = render(
      <PromptCapabilityNotices capability={{ ...base, has_findings: false, notices: [] }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('lists every notice it is given', () => {
    render(<PromptCapabilityNotices capability={{
      ...base,
      notices: [notice('warning', 'First finding.'), notice('info', 'Second finding.')],
    }} />);
    expect(screen.getByText('First finding.')).toBeTruthy();
    expect(screen.getByText('Second finding.')).toBeTruthy();
  });

  it('announces a refusal as an alert, not a status', () => {
    render(<PromptCapabilityNotices capability={{
      ...base,
      would_refuse_single_call: true,
      notices: [notice('error', 'Asks for an output this platform cannot produce.')],
    }} />);
    const panel = screen.getByRole('alert');
    expect(panel.textContent).toContain('cannot be used for generation');
  });

  it('uses a status role and a comparison heading when nothing is refused', () => {
    render(<PromptCapabilityNotices capability={base} />);
    const panel = screen.getByRole('status');
    expect(panel.textContent).toContain('compares with what the pipeline emits');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('keeps the refusal styling distinct from a mere warning', () => {
    const { container: refused } = render(<PromptCapabilityNotices capability={{
      ...base, would_refuse_single_call: true, notices: [notice('error', 'Refused.')],
    }} />);
    const { container: warned } = render(<PromptCapabilityNotices capability={base} />);
    const cls = (c) => c.firstChild.getAttribute('class');
    expect(cls(refused)).not.toEqual(cls(warned));
  });

  it('tags each notice with its severity for styling and assistive tech', () => {
    render(<PromptCapabilityNotices capability={{
      ...base,
      notices: [notice('info', 'Matched 9 of 13 requested column(s).')],
    }} />);
    expect(screen.getByText('Matched 9 of 13 requested column(s).')
      .getAttribute('data-severity')).toBe('info');
  });
});
