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

  it('lists every actionable notice it is given', () => {
    render(<PromptCapabilityNotices capability={{
      ...base,
      notices: [notice('warning', 'First finding.'), notice('error', 'Second finding.')],
    }} />);
    expect(screen.getByText('First finding.')).toBeTruthy();
    expect(screen.getByText('Second finding.')).toBeTruthy();
  });

  // The picker interrupts someone mid-task. Confirmation that nothing is wrong reads
  // as a warning to a reader who does not know a reconciliation runs at all, so it
  // stays in provenance and in the generated document instead.
  it('says nothing when every finding is informational', () => {
    const { container } = render(<PromptCapabilityNotices capability={{
      ...base,
      notices: [notice('info', 'Matched 32 of 32 requested column(s).'),
                notice('info', 'Emitting 1 additional day-table column(s).')],
    }} />);
    expect(container.firstChild).toBeNull();
  });

  it('keeps an actionable notice visible alongside informational ones', () => {
    render(<PromptCapabilityNotices capability={{
      ...base,
      notices: [notice('info', 'Matched 32 of 32 requested column(s).'),
                notice('warning', 'Requests 4 day-table column(s) not emitted.')],
    }} />);
    expect(screen.getByText(/not emitted/)).toBeTruthy();
    expect(screen.queryByText(/Matched 32 of 32/)).toBeNull();
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
      notices: [notice('warning', 'Requests 4 day-table column(s) not emitted.')],
    }} />);
    expect(screen.getByText('Requests 4 day-table column(s) not emitted.')
      .getAttribute('data-severity')).toBe('warning');
  });
});

// One picker feeds two Generate buttons that produce different documents. Placing a
// notice beside the wrong one is not cosmetic: "Matched 32 of 32 day-table columns"
// above the single-call button describes a table that button never emits.
describe('PromptCapabilityNotices — scope', () => {
  const scoped = (severity, scope, message) => ({ severity, scope, message });
  const mixed = {
    ...base,
    notices: [
      scoped('info', 'block', 'Matched 32 of 32 requested column(s).'),
      scoped('warning', 'block', 'Requests 4 day-table column(s) not emitted.'),
      scoped('warning', 'single', 'No {{extra_instructions_block}} slot.'),
      scoped('warning', 'both', '1 template variable(s) this platform never supplies.'),
    ],
  };

  it('shows only the block-wide notices beside the block-wide button', () => {
    render(<PromptCapabilityNotices capability={mixed} scope="block" />);
    expect(screen.getByText(/not emitted/)).toBeTruthy();
    expect(screen.getByText(/never supplies/)).toBeTruthy();
    expect(screen.queryByText(/extra_instructions_block/)).toBeNull();
  });

  it('shows only the single-call notices beside the single-call button', () => {
    render(<PromptCapabilityNotices capability={mixed} scope="single" />);
    expect(screen.getByText(/extra_instructions_block/)).toBeTruthy();
    expect(screen.getByText(/never supplies/)).toBeTruthy();
    expect(screen.queryByText(/not emitted/)).toBeNull();
  });

  it('renders nothing where no notice applies, rather than an empty box', () => {
    const blockOnly = { ...base, notices: [scoped('warning', 'block', 'Requests 4 not emitted.')] };
    const { container } = render(
      <PromptCapabilityNotices capability={blockOnly} scope="single" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('names the block-wide pipeline in its own heading', () => {
    render(<PromptCapabilityNotices capability={mixed} scope="block" />);
    expect(screen.getByText(/block-wide pipeline emits/)).toBeTruthy();
  });

  it('filters no scope without one, so an unscoped caller loses no actionable notice', () => {
    render(<PromptCapabilityNotices capability={mixed} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('keeps a refusal visible beside the single-call button', () => {
    const refusing = {
      ...base,
      would_refuse_single_call: true,
      notices: [scoped('error', 'both', 'asks for an output this platform cannot produce')],
    };
    render(<PromptCapabilityNotices capability={refusing} scope="single" />);
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText(/cannot be used for generation/)).toBeTruthy();
  });

  it('drops the refusal headline for the block-wide path but keeps the error', () => {
    // That path does run; it refuses only if it falls back to single-call. Claiming
    // "cannot be used" beside a button that works would be the opposite error.
    const refusing = {
      ...base,
      would_refuse_single_call: true,
      notices: [scoped('error', 'both', 'asks for an output this platform cannot produce')],
    };
    render(<PromptCapabilityNotices capability={refusing} scope="block" />);
    expect(screen.queryByText(/cannot be used for generation/)).toBeNull();
    expect(screen.getByText(/cannot produce/)).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
  });
});
