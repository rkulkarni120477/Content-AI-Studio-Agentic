import { createAsyncThunk } from '@reduxjs/toolkit';
import { extractErrorMessage } from '@utils/helpers';
import toast from 'react-hot-toast';

/**
 * Archive / restore / permanently-delete thunks for design documents.
 *
 * CDDs and Blueprints have the same lifecycle and the same hazards, so they get
 * one implementation rather than two that can drift. The server enforces the
 * rules (see app/services/design_doc_archive.py); this layer's job is to make a
 * refusal legible instead of a red toast, and to keep the list truthful after a
 * mutation.
 *
 * After any successful change the list is refetched from the server rather than
 * patched in place. Archiving can unpin a course as a side effect, and a locally
 * spliced array would not know that — a stale "Active" badge on a document the
 * server no longer considers active is exactly the kind of lie that costs
 * someone a regeneration.
 */

/** The server's code for "something still points at this" (409). */
export const RESOURCE_IN_USE = 'RESOURCE_IN_USE';

/** Read the AppError envelope — `{ error: { code, message, detail } }`. */
function appError(e) {
  return e?.response?.data?.error ?? e?.data?.error ?? null;
}

/**
 * Reasons the server refused, as a list of sentences.
 *
 * Falls back to the plain message so a refusal is never rendered as an empty
 * bullet list.
 */
export function refusalBlockers(e) {
  const err = appError(e);
  const blockers = err?.detail?.blockers;
  if (Array.isArray(blockers) && blockers.length) return blockers;
  const message = extractErrorMessage(e);
  return message ? [message] : [];
}

/**
 * True when archiving was refused only because the document is pinned.
 *
 * Distinguished from other refusals because it is the one the user can resolve
 * from the same dialog — by confirming the unpin — rather than by going and
 * changing something else first.
 */
function isPinRefusal(e) {
  const err = appError(e);
  if (err?.code !== RESOURCE_IN_USE) return false;
  return refusalBlockers(e).some((b) => /pinned as active/i.test(b));
}

/**
 * Build the four thunks for one document kind.
 *
 * @param {string}   name     slice name, e.g. 'cdd' — only used for action types
 * @param {string}   label    user-facing noun for toasts, e.g. 'CDD'
 * @param {object}   api      { archive, restore, purge, bulkArchive }
 * @param {Function} refetch  thunk creator run after every successful change,
 *                            called with the course id
 */
export function createArchiveThunks({ name, label, api, refetch }) {
  const reload = async (dispatch, courseId) => {
    if (refetch) await dispatch(refetch(courseId));
  };

  const archiveThunk = createAsyncThunk(
    `${name}/archive`,
    async ({ id, courseId, unpin = false }, { dispatch, rejectWithValue }) => {
      try {
        const res = await api.archive(id, { unpin });
        toast.success(res?.message || `${label} archived.`);
        await reload(dispatch, courseId);
        return { id, ...res };
      } catch (e) {
        // A pin refusal is a question, not a failure: the caller re-asks with
        // unpin set. Surfaced as structured data so the page can offer that
        // instead of showing a dead end.
        if (isPinRefusal(e)) {
          return rejectWithValue({
            id,
            needsUnpin: true,
            blockers: refusalBlockers(e),
            message: extractErrorMessage(e),
          });
        }
        const message = extractErrorMessage(e);
        toast.error(message);
        return rejectWithValue({ id, blockers: refusalBlockers(e), message });
      }
    },
  );

  const restoreThunk = createAsyncThunk(
    `${name}/restore`,
    async ({ id, courseId }, { dispatch, rejectWithValue }) => {
      try {
        const res = await api.restore(id);
        toast.success(res?.message || `${label} restored.`);
        await reload(dispatch, courseId);
        return { id, ...res };
      } catch (e) {
        const message = extractErrorMessage(e);
        toast.error(message);
        return rejectWithValue({ id, message });
      }
    },
  );

  const purgeThunk = createAsyncThunk(
    `${name}/purge`,
    async ({ id, courseId }, { dispatch, rejectWithValue }) => {
      try {
        const res = await api.purge(id);
        toast.success(res?.message || `${label} permanently deleted.`);
        await reload(dispatch, courseId);
        return { id, ...res };
      } catch (e) {
        // Every purge refusal names what is in the way, and the user cannot
        // override it from here — the fix is to detach the dependents first.
        const blockers = refusalBlockers(e);
        toast.error(blockers[0] || extractErrorMessage(e));
        return rejectWithValue({ id, blockers, message: extractErrorMessage(e) });
      }
    },
  );

  const bulkArchiveThunk = createAsyncThunk(
    `${name}/bulkArchive`,
    async ({ ids, courseId, projectId, unpin = false }, { dispatch, rejectWithValue }) => {
      try {
        const res = await api.bulkArchive({ ids, unpin, courseId, projectId });
        const archived = res?.archived ?? 0;
        const skipped = res?.skipped ?? 0;
        // Reported together on purpose. Rounding a partial result to "done"
        // hides the ones that were left behind, and the skipped ones are
        // precisely the ones that needed a human decision.
        toast.success(
          skipped
            ? `Archived ${archived}; skipped ${skipped}. Open the archived list to review.`
            : `Archived ${archived} ${label}${archived === 1 ? '' : 's'}.`,
        );
        await reload(dispatch, courseId);
        return res;
      } catch (e) {
        const message = extractErrorMessage(e);
        toast.error(message);
        return rejectWithValue({ message });
      }
    },
  );

  return { archiveThunk, restoreThunk, purgeThunk, bulkArchiveThunk };
}

/**
 * Wire the shared archive thunks into a slice's extraReducers.
 *
 * Only the busy flag and the error live here — the list itself is owned by the
 * refetch each thunk performs, so there is exactly one source of truth for what
 * the list contains.
 */
export function attachArchiveReducers(builder, thunks, { busyFlag = 'isArchiving' } = {}) {
  const all = [
    thunks.archiveThunk, thunks.restoreThunk, thunks.purgeThunk, thunks.bulkArchiveThunk,
  ];
  all.forEach((thunk) => {
    builder
      .addCase(thunk.pending, (s) => { s[busyFlag] = true; s.archiveRefusal = null; })
      .addCase(thunk.fulfilled, (s) => { s[busyFlag] = false; })
      .addCase(thunk.rejected, (s, { payload }) => {
        s[busyFlag] = false;
        // Kept out of `s.error`: the page-level error banner replaces the whole
        // view, and a refused archive should leave the list on screen so the
        // user can act on the reason.
        s.archiveRefusal = payload ?? null;
      });
  });
}
