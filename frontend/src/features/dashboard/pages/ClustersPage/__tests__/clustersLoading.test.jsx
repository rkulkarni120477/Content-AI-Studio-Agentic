// @vitest-environment jsdom
//
// "No categories" must never be shown before a cluster fetch has settled.
//
// Opening /projects/23/clusters showed the empty state for a beat and then the
// grid. Two faults combined:
//
//   1. the page fetched clusters on mount, racing the project sync — and
//      setSelectedProject clears `clusters` on a project change, so a list that
//      had already arrived was thrown away with nothing marking it as gone;
//   2. `clusters.items: []` means both "not loaded yet" and "this project has
//      no categories", and `isLoadingClusters` is false until the fetch's
//      pending action lands, which is a paint later than the decision to fetch.
//
// So the page could sit on empty + not-loading + no-error and conclude there
// were no categories. In dev StrictMode's second dispatch covered it up a
// moment later; in a production build the wrong answer could have stuck.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';

const getProject = vi.fn();
const listClusters = vi.fn();

vi.mock('@features/dashboard/services/dashboardService', () => ({
  dashboardService: {
    getProject: (...a) => getProject(...a),
    listClusters: (...a) => listClusters(...a),
    createCluster: () => Promise.resolve({}),
    deleteCluster: () => Promise.resolve({}),
  },
}));

vi.mock('@hooks/useAuth', () => ({
  useAuth: () => ({
    hasPermission: () => true,
    isAdmin: true,
    isPlatformAdmin: true,
    projectId: null,
  }),
}));

// Layout and modals are irrelevant to the loading question and drag in the
// whole sidebar tree; the page's own branch is what is under test.
vi.mock('@components/layout/SelectionLayout/SelectionLayout', () => ({
  default: ({ children }) => <div>{children}</div>,
}));
vi.mock('@components/cluster/ClusterPromptManager/ClusterPromptManager', () => ({
  default: () => null,
}));
vi.mock('@features/dashboard/components/EditEntityModal/EditEntityModal', () => ({
  default: () => null,
}));
vi.mock('@components/common/ConfirmDialog/ConfirmDialog', () => ({
  default: () => null,
}));

// The flash is transient: the empty state mounts and is replaced a tick later,
// so asserting on the final DOM cannot see it (RTL flushes effects inside the
// same act()). Recording every mount can — an empty state that appeared for one
// commit and vanished still lands in this list.
const emptyStatesRendered = [];
vi.mock('@components/common/EmptyState/EmptyState', () => ({
  default: ({ title, message }) => {
    emptyStatesRendered.push(title);
    return <div>{title}{message ? ` — ${message}` : ''}</div>;
  },
}));

const { default: dashboardReducer, setSelectedProject } = await import('@features/dashboard/dashboardSlice');
const { fetchClustersThunk } = await import('@features/dashboard/dashboardThunks');
const { default: ClustersPage } = await import('../ClustersPage');

const PROJECT = { id: 23, name: 'AIM' };

function cluster(id, name) {
  return { id, name, description: '', course_count: 2 };
}

function renderPage(preload = []) {
  const store = configureStore({ reducer: { dashboard: dashboardReducer } });
  preload.forEach((a) => store.dispatch(a));
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/projects/23/clusters']}>
        <Routes>
          <Route path="/projects/:projectId/clusters" element={<ClustersPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

/** A promise plus the handle to settle it later. */
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => {
  getProject.mockReset();
  listClusters.mockReset();
  emptyStatesRendered.length = 0;
});

/** Assert the "No categories" empty state was never committed, even briefly. */
function expectNeverFlashedEmpty() {
  expect(emptyStatesRendered).not.toContain('No categories');
}

afterEach(cleanup);

describe('ClustersPage — the empty state waits for an answer', () => {
  it('never shows "No categories" while the project and the list are both in flight', async () => {
    getProject.mockResolvedValue(PROJECT);
    listClusters.mockResolvedValue({ items: [cluster(1, 'Aviation')], total: 1 });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Aviation/)).toBeTruthy());
    expectNeverFlashedEmpty();
  });

  it('never shows it when the project is already selected on the first render', async () => {
    // Arriving from another page (or from the sidebar) — selectedProject is
    // already set and its clear has already emptied `clusters`. The page then
    // renders its body on the FIRST paint, before any effect has run, so
    // isLoadingClusters is still false and the list is still empty. This is the
    // deterministic flash: no race needed.
    getProject.mockResolvedValue(PROJECT);
    const clusters = deferred();
    listClusters.mockReturnValue(clusters.promise);

    renderPage([setSelectedProject(PROJECT)]);
    expectNeverFlashedEmpty();

    clusters.resolve({ items: [cluster(1, 'Aviation')], total: 1 });
    await waitFor(() => expect(screen.getByText(/Aviation/)).toBeTruthy());
    expectNeverFlashedEmpty();
  });

  it('shows the loading line, not the empty state, before the list arrives', async () => {
    getProject.mockResolvedValue(PROJECT);
    const clusters = deferred();
    listClusters.mockReturnValue(clusters.promise);

    renderPage();
    await waitFor(() => expect(screen.getByText(/Loading categories/)).toBeTruthy());
    expectNeverFlashedEmpty();

    clusters.resolve({ items: [cluster(1, 'Aviation')], total: 1 });
    await waitFor(() => expect(screen.getByText(/Aviation/)).toBeTruthy());
    expectNeverFlashedEmpty();
  });

  it('still shows "No categories" for a project that really has none', async () => {
    getProject.mockResolvedValue(PROJECT);
    listClusters.mockResolvedValue({ items: [], total: 0 });

    renderPage();
    await waitFor(() => expect(emptyStatesRendered).toContain('No categories'));
  });

  it('surfaces a failed fetch instead of claiming there are no categories', async () => {
    getProject.mockResolvedValue(PROJECT);
    listClusters.mockRejectedValue(new Error('boom'));

    renderPage();
    await waitFor(() => expect(screen.getByText(/Couldn.t load categories/)).toBeTruthy());
    expectNeverFlashedEmpty();
  });

  it('does not fetch the list before the project it belongs to is selected', async () => {
    const project = deferred();
    getProject.mockReturnValue(project.promise);
    listClusters.mockResolvedValue({ items: [], total: 0 });

    renderPage();
    await waitFor(() => expect(getProject).toHaveBeenCalled());
    // A list fetched now would be discarded by setSelectedProject's clear.
    expect(listClusters).not.toHaveBeenCalled();

    project.resolve(PROJECT);
    await waitFor(() => expect(listClusters).toHaveBeenCalledWith(23));
  });
});

describe('dashboardSlice — the loaded marker tracks the project', () => {
  function reduce(actions) {
    const store = configureStore({ reducer: { dashboard: dashboardReducer } });
    actions.forEach((a) => store.dispatch(a));
    return store.getState().dashboard;
  }

  // fulfilled must carry the SAME requestId a prior `pending` recorded in
  // state — the requestId guard (added to stop a superseded fetch response
  // from clobbering a newer one) drops any fulfilled whose requestId doesn't
  // match, and the marker is only set inside that guard.
  const pending = (requestId, projectId) => fetchClustersThunk.pending(requestId, projectId);
  const fulfilled = (requestId, projectId, items) => (
    fetchClustersThunk.fulfilled({ items, total: items.length }, requestId, projectId)
  );

  it('starts out having loaded nothing', () => {
    expect(reduce([]).clustersLoadedFor).toBeNull();
  });

  it('records the project a list was fetched for', () => {
    const state = reduce([pending('r1', 23), fulfilled('r1', 23, [cluster(1, 'A')])]);
    expect(state.clustersLoadedFor).toBe(23);
  });

  it('drops the marker with the data when the project changes', () => {
    const state = reduce([
      setSelectedProject(PROJECT),
      pending('r1', 23),
      fulfilled('r1', 23, [cluster(1, 'A')]),
      setSelectedProject({ id: 29, name: 'Cengage' }),
    ]);
    // The regression: the list was cleared but still read as "loaded, empty".
    expect(state.clustersLoadedFor).toBeNull();
  });

  it('keeps the marker when the same project is re-selected', () => {
    const state = reduce([
      setSelectedProject(PROJECT),
      pending('r1', 23),
      fulfilled('r1', 23, [cluster(1, 'A')]),
      setSelectedProject(PROJECT),
    ]);
    expect(state.clusters.items).toHaveLength(1);
    expect(state.clustersLoadedFor).toBe(23);
  });

  it('a superseded fulfilled cannot set the marker for the wrong project', () => {
    // request-A (project 23) is still in flight when the user has already
    // moved on to project 29 (request-B) by the time A's response lands.
    const state = reduce([
      pending('request-A', 23),
      pending('request-B', 29),
      fulfilled('request-A', 23, [cluster(1, 'A')]),
    ]);
    expect(state.clustersLoadedFor).toBeNull();
    expect(state.isLoadingClusters).toBe(true);
  });
});
