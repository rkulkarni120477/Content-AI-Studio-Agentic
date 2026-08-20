// @vitest-environment jsdom
//
// The Prompt Library lists only CAS pipeline prompts (Phase 12b), so the Delete
// control has to render for prompt_kind='pipeline' — it previously sat behind a
// library-only guard and was therefore dead code on every row on screen. These
// tests pin that, in both views, without letting Delete leak to the roles or
// states that must not have it, and check the untouched actions still render.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import PromptCard from '../PromptCard';
import PromptListRow from '../PromptListRow';

afterEach(cleanup);

const pipelinePrompt = {
  id: 7,
  title: 'CDD Builder',
  content: 'Write a CDD.',
  prompt_kind: 'pipeline',
  tags: [],
  variables: [],
  versions: [],
  archived: false,
  pipeline: { name: 'cdd_builder', component_type: 'cdd', variant: null, is_default: false },
};

const noop = () => {};
const baseProps = {
  onCopy: noop,
  onDuplicate: noop,
  onDelete: noop,
  onRestore: noop,
  onTagClick: noop,
};

function renderCard(props) {
  return render(
    <MemoryRouter>
      <PromptCard prompt={pipelinePrompt} {...baseProps} {...props} />
    </MemoryRouter>,
  );
}

function renderRow(props) {
  return render(
    <MemoryRouter>
      <PromptListRow prompt={pipelinePrompt} {...baseProps} {...props} />
    </MemoryRouter>,
  );
}

describe.each([
  ['Card view', renderCard],
  ['List view', renderRow],
])('%s', (_label, renderView) => {
  it('offers Delete for a pipeline prompt', () => {
    renderView({ isAdmin: true, canDelete: true });
    expect(screen.getByTitle('Delete')).toBeTruthy();
  });

  it('hides Delete when the viewer lacks the pipeline tier', () => {
    // isAdmin (manager tier) is not enough — deleting a pipeline row is admin-only.
    renderView({ isAdmin: true, canDelete: false });
    expect(screen.queryByTitle('Delete')).toBeNull();
  });

  it('hides Delete on an already-archived prompt, offering Restore instead', () => {
    renderView({
      isAdmin: true,
      canDelete: true,
      prompt: { ...pipelinePrompt, archived: true },
    });
    expect(screen.queryByTitle('Delete')).toBeNull();
    expect(screen.getByTitle('Restore from archive')).toBeTruthy();
  });

  it('hands the whole prompt up so the confirmation can name it', () => {
    const onDelete = vi.fn();
    renderView({ isAdmin: true, canDelete: true, onDelete });
    screen.getByTitle('Delete').click();
    expect(onDelete).toHaveBeenCalledWith(pipelinePrompt);
  });

  it('keeps Copy, View and Edit working alongside Delete', () => {
    renderView({ isAdmin: true, canDelete: true });
    expect(screen.getByText(/Copy/)).toBeTruthy();
    expect(screen.getByTitle('View')).toBeTruthy();
    expect(screen.getByTitle('Edit')).toBeTruthy();
  });
});

describe('Duplicate stays library-only', () => {
  it('is not offered for a pipeline prompt', () => {
    renderCard({ isAdmin: true, canDelete: true });
    expect(screen.queryByTitle('Duplicate')).toBeNull();
  });

  it('is still offered for a library prompt', () => {
    renderCard({
      isAdmin: true,
      canDelete: true,
      prompt: { ...pipelinePrompt, prompt_kind: 'library', pipeline: undefined },
    });
    expect(screen.getByTitle('Duplicate')).toBeTruthy();
  });
});
