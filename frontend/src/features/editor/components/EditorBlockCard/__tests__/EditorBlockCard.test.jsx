// @vitest-environment jsdom
//
// Bug: "Submit for Review option is not displayed after saving edited
// content". canSubmit required `genCreatedBy === user.username` — the
// generation's ORIGINAL creator — on top of canEdit. Reproduced live: an
// author (ID) assigned to edit a DRAFT block from a generation someone else
// created could save changes fine (canEdit doesn't check ownership) but the
// whole "Submit for Review" section silently never appeared, no error, no
// matter how many times they edited and saved. The backend's
// submit_for_review (app/api/v1/routers/workflow.py) has no ownership check
// at all — only `require_permission("workflow.submit")`, which admin,
// reviewer AND author all hold (app/core/permissions.py). The frontend
// invented a stricter rule the backend never enforced.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

const dispatchMock = vi.fn(() => ({ unwrap: () => Promise.resolve({}) }));

vi.mock('@app/hooks', () => ({
  useAppDispatch: () => dispatchMock,
  // Both real selectors read a nested path that doesn't exist on this bare
  // fake state; both call sites tolerate the resulting undefined.
  useAppSelector: (selectorOrFn) => {
    try {
      return selectorOrFn({ dashboard: {}, editor: {} });
    } catch {
      return undefined;
    }
  },
}));

vi.mock('@features/editor/editorThunks', () => ({
  updateBlockThunk: (a) => ({ type: 'updateBlock', payload: a }),
  autosaveBlockThunk: (a) => ({ type: 'autosaveBlock', payload: a }),
  regenerateBlockThunk: (a) => ({ type: 'regenerateBlock', payload: a }),
  regenerateBlockItemThunk: (a) => ({ type: 'regenerateBlockItem', payload: a }),
  submitBlockThunk: (a) => ({ type: 'submitBlock', payload: a }),
  triggerPlagiarismThunk: (a) => ({ type: 'triggerPlagiarism', payload: a }),
  fetchPlagiarismStatusThunk: (a) => ({ type: 'fetchPlagiarismStatus', payload: a }),
  restoreBlockVersionThunk: (a) => ({ type: 'restoreBlockVersion', payload: a }),
  createSnapshotThunk: (a) => ({ type: 'createSnapshot', payload: a }),
  scoreBlockThunk: (a) => ({ type: 'scoreBlock', payload: a }),
  fetchGenerationBlocksThunk: (a) => ({ type: 'fetchGenerationBlocks', payload: a }),
}));

vi.mock('@features/editor/services/editorService', () => ({
  editorService: {
    getBlockVersions: () => Promise.resolve([]),
    cleanupAssets: () => Promise.resolve(),
    rateBlock: () => Promise.resolve({}),
  },
}));

vi.mock('@features/editor/components/UnifiedBlockEditor/UnifiedBlockEditor', () => ({
  default: () => null,
}));
vi.mock('@features/editor/components/CompareVersionsModal/CompareVersionsModal', () => ({
  default: () => null,
}));
vi.mock('@features/editor/components/ValidationPanel/ValidationPanel', () => ({
  default: () => null,
}));
vi.mock('@features/editor/components/WorkflowStatusBadge/WorkflowStatusBadge', () => ({
  default: () => null,
}));

const hasPermissionMock = vi.fn();
vi.mock('@hooks/useAuth', () => ({
  useAuth: () => ({
    user: { username: 'author1' },
    isAdmin: false,
    isReviewer: false,
    hasPermission: hasPermissionMock,
  }),
}));

const { default: EditorBlockCard } = await import('../EditorBlockCard');

function draftBlock(overrides = {}) {
  return {
    id: 2038, workflow_state: 'draft', content: 'Some content', block_label: 'Topic',
    ...overrides,
  };
}

afterEach(() => { cleanup(); hasPermissionMock.mockReset(); });

describe('EditorBlockCard — Submit for Review visibility', () => {
  it('shows Submit for Review for an author with workflow.submit, regardless of who created the generation', () => {
    // The exact bug: this author did NOT create the generation (no
    // genCreatedBy prop is even passed anymore — the component must not
    // depend on it), but they hold workflow.submit, same as the backend.
    hasPermissionMock.mockImplementation((perm) => perm === 'workflow.submit');

    render(<EditorBlockCard block={draftBlock()} generationId={99} reviewers={['AIMadmin']} />);

    expect(screen.getAllByText('📤 Submit for Review').length).toBeGreaterThan(0);
  });

  it('hides Submit for Review when the user lacks workflow.submit', () => {
    hasPermissionMock.mockReturnValue(false);

    render(<EditorBlockCard block={draftBlock()} generationId={99} reviewers={['AIMadmin']} />);

    expect(screen.queryByText('📤 Submit for Review')).toBeNull();
  });

  it('hides Submit for Review outside a submittable workflow state, even with workflow.submit', () => {
    hasPermissionMock.mockReturnValue(true);

    render(<EditorBlockCard block={draftBlock({ workflow_state: 'approved' })} generationId={99} reviewers={['AIMadmin']} />);

    expect(screen.queryByText('📤 Submit for Review')).toBeNull();
  });
});
