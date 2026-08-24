/**
 * A refused archive is a question, not a failure.
 *
 * The server refuses two very different things with the same 409:
 *
 *  1. "this CDD is pinned as active" — recoverable right here, by confirming the
 *     unpin. Rendering that as a red toast and nothing else leaves the user with
 *     no way forward except guessing.
 *  2. "blueprints were derived from this" — not recoverable from this dialog at
 *     all, and the reason has to be shown, because the alternative is a delete
 *     button that silently does nothing.
 *
 * These tests pin the difference, and pin that a partial bulk result reports
 * both halves rather than rounding to "done".
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import { createSlice } from '@reduxjs/toolkit';

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

import toast from 'react-hot-toast';
import {
  createArchiveThunks, attachArchiveReducers, refusalBlockers, RESOURCE_IN_USE,
} from '../documentArchive';

/** An axios-shaped rejection carrying the app's AppError envelope. */
function appErrorResponse(status, code, message, detail = {}) {
  const err = new Error(message);
  err.response = { status, data: { error: { code, message, detail } } };
  return err;
}

const PIN_REFUSAL = appErrorResponse(
  409, RESOURCE_IN_USE,
  'This CDD is pinned as active on course 48.',
  { blockers: ['pinned as active on course 48'], id: 1 },
);

const CASCADE_REFUSAL = appErrorResponse(
  409, RESOURCE_IN_USE,
  'This CDD cannot be permanently deleted.',
  {
    blockers: [
      '3 blueprint(s) were derived from it (deleting it would delete them too)',
      '2 generation(s) record it as their source',
    ],
    id: 1,
  },
);

function makeStore(api, refetch) {
  const thunks = createArchiveThunks({ name: 'doc', label: 'CDD', api, refetch });
  const slice = createSlice({
    name: 'doc',
    initialState: { isArchiving: false, archiveRefusal: null },
    reducers: {},
    extraReducers: (b) => attachArchiveReducers(b, thunks),
  });
  const store = configureStore({ reducer: { doc: slice.reducer } });
  return { store, thunks };
}

const okApi = () => ({
  archive: vi.fn().mockResolvedValue({ id: 1, archived: true, message: 'Archived.' }),
  restore: vi.fn().mockResolvedValue({ id: 1, archived: false, message: 'Restored.' }),
  purge: vi.fn().mockResolvedValue({ id: 1, deleted: true, message: 'Deleted.' }),
  bulkArchive: vi.fn().mockResolvedValue({ archived: 2, skipped: 0, already_archived: 0 }),
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('refusalBlockers', () => {
  it('returns the server’s reasons, so a refusal can explain itself', () => {
    expect(refusalBlockers(CASCADE_REFUSAL)).toEqual([
      '3 blueprint(s) were derived from it (deleting it would delete them too)',
      '2 generation(s) record it as their source',
    ]);
  });

  it('falls back to the message rather than rendering an empty list', () => {
    const bare = appErrorResponse(422, 'VALIDATION_ERROR', 'Archive it first.');
    expect(refusalBlockers(bare)).toEqual(['Archive it first.']);
  });
});

describe('archive', () => {
  it('archives and refreshes the list', async () => {
    const api = okApi();
    const refetch = vi.fn(() => () => Promise.resolve());
    const { store, thunks } = makeStore(api, refetch);

    await store.dispatch(thunks.archiveThunk({ id: 1, courseId: 48 }));

    expect(api.archive).toHaveBeenCalledWith(1, { unpin: false });
    // Refetched, not spliced: archiving can unpin a course as a side effect,
    // and a locally-patched array would not know that.
    expect(refetch).toHaveBeenCalledWith(48);
    expect(store.getState().doc.isArchiving).toBe(false);
    expect(store.getState().doc.archiveRefusal).toBeNull();
  });

  it('flags a pin refusal as answerable instead of shouting it', async () => {
    const api = { ...okApi(), archive: vi.fn().mockRejectedValue(PIN_REFUSAL) };
    const { store, thunks } = makeStore(api, vi.fn(() => () => Promise.resolve()));

    await store.dispatch(thunks.archiveThunk({ id: 1, courseId: 48 }));

    const refusal = store.getState().doc.archiveRefusal;
    expect(refusal.needsUnpin).toBe(true);
    expect(refusal.blockers).toEqual(['pinned as active on course 48']);
    // No error toast: the UI asks the question, it does not report a failure.
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('passes unpin through when the user confirms', async () => {
    const api = okApi();
    const { store, thunks } = makeStore(api, vi.fn(() => () => Promise.resolve()));

    await store.dispatch(thunks.archiveThunk({ id: 1, courseId: 48, unpin: true }));

    expect(api.archive).toHaveBeenCalledWith(1, { unpin: true });
  });

  it('treats a non-pin refusal as a real error', async () => {
    const api = { ...okApi(), archive: vi.fn().mockRejectedValue(CASCADE_REFUSAL) };
    const { store, thunks } = makeStore(api, vi.fn(() => () => Promise.resolve()));

    await store.dispatch(thunks.archiveThunk({ id: 1, courseId: 48 }));

    const refusal = store.getState().doc.archiveRefusal;
    expect(refusal.needsUnpin).toBeUndefined();
    expect(toast.error).toHaveBeenCalled();
  });

  it('clears a previous refusal when a new attempt starts', async () => {
    const failing = { ...okApi(), archive: vi.fn().mockRejectedValue(CASCADE_REFUSAL) };
    const { store, thunks } = makeStore(failing, vi.fn(() => () => Promise.resolve()));
    await store.dispatch(thunks.archiveThunk({ id: 1, courseId: 48 }));
    expect(store.getState().doc.archiveRefusal).not.toBeNull();

    failing.archive = vi.fn().mockResolvedValue({ id: 1, archived: true });
    await store.dispatch(thunks.archiveThunk({ id: 1, courseId: 48 }));

    expect(store.getState().doc.archiveRefusal).toBeNull();
  });
});

describe('purge', () => {
  it('surfaces the blocker, not a generic failure', async () => {
    const api = { ...okApi(), purge: vi.fn().mockRejectedValue(CASCADE_REFUSAL) };
    const { store, thunks } = makeStore(api, vi.fn(() => () => Promise.resolve()));

    await store.dispatch(thunks.purgeThunk({ id: 1, courseId: 48 }));

    expect(toast.error).toHaveBeenCalledWith(
      '3 blueprint(s) were derived from it (deleting it would delete them too)',
    );
    expect(store.getState().doc.archiveRefusal.blockers).toHaveLength(2);
  });
});

describe('bulk archive', () => {
  it('reports what was skipped rather than rounding to done', async () => {
    const api = {
      ...okApi(),
      bulkArchive: vi.fn().mockResolvedValue({ archived: 9, skipped: 3, already_archived: 0 }),
    };
    const { store, thunks } = makeStore(api, vi.fn(() => () => Promise.resolve()));

    await store.dispatch(thunks.bulkArchiveThunk({ ids: [1, 2, 3], courseId: 48 }));

    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining('skipped 3'),
    );
  });

  it('says nothing about skips when there were none', async () => {
    const api = okApi();
    const { store, thunks } = makeStore(api, vi.fn(() => () => Promise.resolve()));

    await store.dispatch(thunks.bulkArchiveThunk({ ids: [1, 2], courseId: 48 }));

    expect(toast.success).toHaveBeenCalledWith(expect.not.stringContaining('skipped'));
  });

  it('forwards the scope so a stale id list cannot reach another workspace', async () => {
    const api = okApi();
    const { store, thunks } = makeStore(api, vi.fn(() => () => Promise.resolve()));

    await store.dispatch(thunks.bulkArchiveThunk({
      ids: [1, 2], courseId: 48, projectId: 7,
    }));

    expect(api.bulkArchive).toHaveBeenCalledWith({
      ids: [1, 2], unpin: false, courseId: 48, projectId: 7,
    });
  });
});
