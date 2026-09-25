// @vitest-environment jsdom
//
// Loading behaviour of the Prompt Library list (AC 1/2/3/5 of the load-time
// ticket). Three properties are pinned here, all of which were broken before:
//   1. typing fires ONE request per debounce window, not one per keystroke —
//      each request is a full list load;
//   2. only the newest response reaches the screen, whatever order the
//      responses land in (the page used to render whichever arrived last);
//   3. a refetch keeps the rows already on screen instead of blanking them
//      back to the "Loading…" placeholder.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const fetchPrompts = vi.fn();
const show = vi.fn();

vi.mock('../../api/prompts', () => ({
  fetchPrompts: (...args) => fetchPrompts(...args),
  fetchMeta: () => Promise.resolve({ categories: [], tags: [] }),
  fetchPromptUsage: () => Promise.resolve({ blocking: false }),
  deletePrompt: () => Promise.resolve({}),
  duplicatePrompt: () => Promise.resolve({}),
  markPromptUsed: () => Promise.resolve(),
  restorePrompt: () => Promise.resolve({}),
}));
vi.mock('../../api/flow', () => ({ fetchPromptsByCourse: () => Promise.resolve([]) }));
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'admin', role: 'admin', permissions: [] }, loading: false }),
}));
vi.mock('../../context/ToastContext', () => ({ useToast: () => ({ show }) }));

const { default: PromptListPage } = await import('../PromptListPage');

function row(id, title) {
  return {
    id,
    title,
    content: `body ${id}`,
    description: '',
    category: '',
    prompt_kind: 'pipeline',
    tags: [],
    variables: [],
    archived: false,
    _version_count: 1,
    _child_count: 0,
    _review_stats: { count: 0, avg: 0 },
    pipeline: { name: title, component_type: 'cdd', variant: null, is_default: false },
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PromptListPage />
    </MemoryRouter>,
  );
}

// Keystroke-by-keystroke typing (no user-event dependency in this project):
// each character is its own change event, so the debounce is really exercised.
function type(input, text) {
  let value = input.value;
  for (const ch of text) {
    value += ch;
    fireEvent.change(input, { target: { value } });
  }
}

// A resolver we can settle by hand, so response ORDER is under the test's control.
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => {
  fetchPrompts.mockReset();
  show.mockReset();
  localStorage.setItem('plib_view', 'card');
});
afterEach(cleanup);

describe('Prompt Library list loading', () => {
  it('renders the placeholder until the first load lands, then the rows', async () => {
    const first = deferred();
    fetchPrompts.mockReturnValueOnce(first.promise);
    renderPage();

    expect(screen.getByText('Loading…')).toBeTruthy();
    first.resolve([row(1, 'Alpha')]);
    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());
    expect(screen.queryByText('Loading…')).toBeNull();
  });

  it('still shows the placeholder after a failed first load', async () => {
    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    renderPage();
    await waitFor(() => expect(show).toHaveBeenCalledWith('Could not load prompts.'));

    // A first load that never delivered rows must not retire the placeholder:
    // the next attempt reads as loading, not as an empty library.
    const pending = deferred();
    fetchPrompts.mockReturnValueOnce(pending.promise);
    type(screen.getByLabelText('Search prompts'), 'z');
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(screen.getByText('Loading…')).toBeTruthy();

    pending.resolve([row(1, 'Alpha')]);
    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());
  });

  it('collapses a burst of keystrokes into a single request', async () => {
    fetchPrompts.mockResolvedValue([row(1, 'Alpha')]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());
    expect(fetchPrompts).toHaveBeenCalledTimes(1);

    type(screen.getByLabelText('Search prompts'), 'cdd');
    // Debounce window still open: no extra request for any of the 3 keystrokes.
    expect(fetchPrompts).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(fetchPrompts.mock.calls[1][0]).toMatchObject({ q: 'cdd', kind: 'pipeline' });
    // …and the request is cancellable, so a superseded load stops on the wire.
    expect(fetchPrompts.mock.calls[1][1].signal).toBeInstanceOf(AbortSignal);
  });

  it('keeps the current rows on screen while refetching', async () => {
    fetchPrompts.mockResolvedValueOnce([row(1, 'Alpha')]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());

    const pending = deferred();
    fetchPrompts.mockReturnValueOnce(pending.promise);
    type(screen.getByLabelText('Search prompts'), 'x');
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2), { timeout: 2000 });

    // Mid-refetch: the previous result is still readable, not replaced by the
    // placeholder, and the region is marked busy for assistive tech.
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.queryByText('Loading…')).toBeNull();
    expect(document.querySelector('.page-card').getAttribute('aria-busy')).toBe('true');

    pending.resolve([row(2, 'Beta')]);
    await waitFor(() => expect(screen.getByText('Beta')).toBeTruthy());
    expect(document.querySelector('.page-card').getAttribute('aria-busy')).toBe('false');
  });

  it('discards a superseded response that lands after a newer one', async () => {
    fetchPrompts.mockResolvedValueOnce([row(1, 'Alpha')]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());

    const slowStale = deferred();
    const fastFresh = deferred();
    fetchPrompts.mockReturnValueOnce(slowStale.promise).mockReturnValueOnce(fastFresh.promise);

    const search = screen.getByLabelText('Search prompts');
    type(search, 'a');
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2), { timeout: 2000 });
    type(search, 'b');
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(3), { timeout: 2000 });

    // Newest query answers first, the abandoned one answers later.
    fastFresh.resolve([row(3, 'Fresh')]);
    await waitFor(() => expect(screen.getByText('Fresh')).toBeTruthy());
    slowStale.resolve([row(9, 'Stale')]);

    await waitFor(() => expect(screen.getByText('Fresh')).toBeTruthy());
    expect(screen.queryByText('Stale')).toBeNull();
    // The stale load was aborted when the newer one started.
    expect(fetchPrompts.mock.calls[1][1].signal.aborted).toBe(true);
  });
});

// AC 5: a failed load used to fall through to the empty-library state, whose
// "Try adjusting the search or filters" copy sends the user after a problem
// that is not there. It must read as a failure, with a way to retry.
describe('Prompt Library list load failure', () => {
  const EMPTY_COPY = /No prompt titles found/;

  it('shows a load error with Retry, not the empty-library state', async () => {
    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    renderPage();

    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByText("Couldn't load prompts")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(screen.queryByText(EMPTY_COPY)).toBeNull();
    expect(screen.getByRole('button', { name: /Export CSV/ }).disabled).toBe(true);
  });

  it('Retry refetches and replaces the error with the rows', async () => {
    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy());

    fetchPrompts.mockResolvedValueOnce([row(1, 'Alpha')]);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());
    expect(fetchPrompts).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('a failed refetch replaces the stale rows with the error', async () => {
    fetchPrompts.mockResolvedValueOnce([row(1, 'Alpha')]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());

    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    type(screen.getByLabelText('Search prompts'), 'z');

    await waitFor(() => expect(screen.getByText("Couldn't load prompts")).toBeTruthy(), { timeout: 2000 });
    expect(screen.queryByText('Alpha')).toBeNull();
    expect(screen.queryByText(EMPTY_COPY)).toBeNull();

    // While the retry is in flight the error stays up — the empty-library copy
    // must not flash in between.
    const retry = deferred();
    fetchPrompts.mockReturnValueOnce(retry.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(3));
    expect(screen.queryByText(EMPTY_COPY)).toBeNull();
    retry.resolve([row(2, 'Beta')]);
    await waitFor(() => expect(screen.getByText('Beta')).toBeTruthy());
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('a genuinely empty result still shows the empty state, not the error', async () => {
    fetchPrompts.mockResolvedValueOnce([]);
    renderPage();

    await waitFor(() => expect(screen.getByText(EMPTY_COPY)).toBeTruthy());
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('a load cancelled by a newer search does not show the error', async () => {
    fetchPrompts.mockResolvedValueOnce([row(1, 'Alpha')]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());

    // The superseded request rejects the way fetch does when its signal aborts.
    fetchPrompts.mockImplementationOnce((_params, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));
    const fresh = deferred();
    fetchPrompts.mockReturnValueOnce(fresh.promise);

    const search = screen.getByLabelText('Search prompts');
    type(search, 'a');
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2), { timeout: 2000 });
    type(search, 'b');
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(3), { timeout: 2000 });

    fresh.resolve([row(3, 'Fresh')]);
    await waitFor(() => expect(screen.getByText('Fresh')).toBeTruthy());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(show).not.toHaveBeenCalled();
  });
});
