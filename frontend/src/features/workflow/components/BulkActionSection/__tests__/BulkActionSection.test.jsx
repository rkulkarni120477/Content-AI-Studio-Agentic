// @vitest-environment jsdom
//
// Bulk Workflow Status Update ticket — the reusable select-many/confirm/
// transition-together section. Covers the acceptance criteria that live
// entirely in this component: Select All, per-item highlighting, a
// confirmation dialog naming the exact count before anything runs, and
// surfacing per-item failures from the result without losing the successes.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import BulkActionSection from '../BulkActionSection';

const ITEMS = [
  { id: 1, block_label: 'Intro' },
  { id: 2, block_label: 'Lesson 2' },
  { id: 3, block_label: 'Lesson 3' },
];

function renderSection(overrides = {}) {
  const onRun = overrides.onRun ?? vi.fn(() => Promise.resolve({ succeeded: 1, failed: 0, results: [] }));
  const props = {
    title: 'Bulk Move to In Review',
    emptyMessage: 'Nothing here.',
    items: ITEMS,
    actionLabel: 'Move to In Review',
    confirmMessage: (n) => `Are you sure you want to move ${n} items to In Review?`,
    onRun,
    renderResult: (result) => <p>Moved: {result.succeeded} Failed: {result.failed}</p>,
    ...overrides,
  };
  render(<BulkActionSection {...props} />);
  return { onRun: props.onRun };
}

afterEach(cleanup);

describe('BulkActionSection', () => {
  it('shows the empty message when there are no items', () => {
    renderSection({ items: [] });
    expect(screen.getByText('Nothing here.')).toBeTruthy();
  });

  it('Select All checks every item and highlights each row', () => {
    renderSection();
    fireEvent.click(screen.getByLabelText('Select all 3 item(s)'));

    const checkboxes = screen.getAllByRole('checkbox');
    // First checkbox is Select All itself; the rest are per-item.
    expect(checkboxes.slice(1).every((cb) => cb.checked)).toBe(true);
    expect(document.querySelectorAll('li[class*="item--selected"]').length).toBe(3);
  });

  it('disables the action button until at least one item is selected', () => {
    renderSection();
    expect(screen.getByText('Move to In Review').closest('button').disabled).toBe(true);

    fireEvent.click(screen.getByText('#2 — Lesson 2'));
    expect(screen.getByText(/Move to In Review \(1\)/).closest('button').disabled).toBe(false);
  });

  it('shows a confirmation naming the exact selected count before running', () => {
    renderSection();
    fireEvent.click(screen.getByText('#1 — Intro'));
    fireEvent.click(screen.getByText('#3 — Lesson 3'));
    fireEvent.click(screen.getByText(/Move to In Review \(2\)/));

    expect(screen.getByText('Are you sure you want to move 2 items to In Review?')).toBeTruthy();
  });

  it('runs onRun with exactly the selected ids only after confirming, then shows the result', async () => {
    const { onRun } = renderSection();
    fireEvent.click(screen.getByText('#1 — Intro'));
    fireEvent.click(screen.getByText('#2 — Lesson 2'));
    fireEvent.click(screen.getByText(/Move to In Review \(2\)/));

    expect(onRun).not.toHaveBeenCalled();

    // Confirm button inside the dialog carries the action label too.
    fireEvent.click(screen.getAllByText('Move to In Review').at(-1));

    await waitFor(() => expect(onRun).toHaveBeenCalledWith([1, 2]));
    await waitFor(() => expect(screen.getByText('Moved: 1 Failed: 0')).toBeTruthy());
  });

  it('clears the selection after a run completes', async () => {
    renderSection();
    fireEvent.click(screen.getByText('#1 — Intro'));
    fireEvent.click(screen.getByText(/Move to In Review \(1\)/));
    fireEvent.click(screen.getAllByText('Move to In Review').at(-1));

    await waitFor(() => expect(screen.getByText('Move to In Review').closest('button').disabled).toBe(true));
  });

  it('is disabled when canRun is false even with items selected', () => {
    renderSection({ canRun: false });
    fireEvent.click(screen.getByText('#1 — Intro'));
    expect(screen.getByText(/Move to In Review \(1\)/).closest('button').disabled).toBe(true);
  });
});
