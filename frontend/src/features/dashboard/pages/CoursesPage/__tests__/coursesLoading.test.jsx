// @vitest-environment jsdom
//
// Mirrors ClustersPage/__tests__/clustersLoading.test.jsx, one navigation
// level down. Round-4 PR review (finding 2): with `setSelectedCluster` no
// longer clearing `courses`, navigating cluster A -> B could paint A's
// titles under B's header before the new fetch's pending/fulfilled ever
// land, because CoursesPage gated only on isLoadingCourses. coursesLoadedFor
// (+ gating the fetch effect on selCluster.id === cid) closes that gap the
// same way clustersLoadedFor closed it for ClustersPage.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';

const getProject = vi.fn();
const listClusters = vi.fn();
const listCourses = vi.fn();

vi.mock('@features/dashboard/services/dashboardService', () => ({
  dashboardService: {
    getProject: (...a) => getProject(...a),
    listClusters: (...a) => listClusters(...a),
    listCourses: (...a) => listCourses(...a),
    createCourse: () => Promise.resolve({}),
    deleteCourse: () => Promise.resolve({}),
    permanentlyDeleteCourse: () => Promise.resolve({}),
  },
}));

vi.mock('@features/import/services/importService', () => ({
  importService: { health: () => Promise.resolve({ enabled: false }) },
}));

vi.mock('@hooks/useAuth', () => ({
  useAuth: () => ({
    hasPermission: () => true,
    isAdmin: true,
    isPlatformAdmin: true,
    role: 'admin',
    projectId: null,
  }),
}));

vi.mock('@components/layout/SelectionLayout/SelectionLayout', () => ({
  default: ({ children }) => <div>{children}</div>,
}));
vi.mock('@features/dashboard/components/EditEntityModal/EditEntityModal', () => ({ default: () => null }));
vi.mock('@features/dashboard/components/ManageUsersModal/ManageUsersModal', () => ({ default: () => null }));
vi.mock('@features/dashboard/components/CreateCourseModal/CreateCourseModal', () => ({ default: () => null }));
vi.mock('@components/common/ConfirmDialog/ConfirmDialog', () => ({ default: () => null }));

const emptyStatesRendered = [];
vi.mock('@components/common/EmptyState/EmptyState', () => ({
  default: ({ title, message }) => {
    emptyStatesRendered.push(title);
    return <div>{title}{message ? ` — ${message}` : ''}</div>;
  },
}));

const { default: dashboardReducer, setSelectedProject, setSelectedCluster } = await import(
  '@features/dashboard/dashboardSlice'
);
const { fetchCoursesThunk, fetchClustersThunk } = await import('@features/dashboard/dashboardThunks');
const { default: CoursesPage } = await import('../CoursesPage');

const PROJECT = { id: 23, name: 'AIM' };
const CLUSTER_A = { id: 1, name: 'Cluster A' };
const CLUSTER_B = { id: 2, name: 'Cluster B' };

function course(id, name) {
  return { id, name, description: '', is_active: true };
}

function renderAt(path, preload = []) {
  const store = configureStore({ reducer: { dashboard: dashboardReducer } });
  preload.forEach((a) => store.dispatch(a));
  const utils = render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/projects/:projectId/clusters/:clusterId/courses" element={<CoursesPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
  return { store, ...utils };
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => {
  getProject.mockReset();
  listClusters.mockReset();
  listCourses.mockReset();
  emptyStatesRendered.length = 0;
});
afterEach(cleanup);

describe('CoursesPage — a cluster switch cannot paint the old cluster´s titles', () => {
  it('never renders cluster A´s course under cluster B´s header', async () => {
    // Arrives at cluster B's URL carrying redux state left over from cluster
    // A: selectedCluster still A, courses still A's items, coursesLoadedFor
    // still 1. Without the marker gate this paints "Course From A" under
    // cluster B's header on the very first render, before the sync effect
    // (which resolves cluster B and re-fetches) has done anything.
    listClusters.mockResolvedValue({ items: [CLUSTER_A, CLUSTER_B], total: 2 });
    const forB = deferred();
    listCourses.mockReturnValueOnce(forB.promise);

    renderAt('/projects/23/clusters/2/courses', [
      setSelectedProject(PROJECT),
      setSelectedCluster(CLUSTER_A),
      fetchCoursesThunk.pending('r1', 1),
      fetchCoursesThunk.fulfilled({ items: [course(10, 'Course From A')], total: 1 }, 'r1', 1),
    ]);

    expect(screen.queryByText('Course From A')).toBeNull();
    expect(screen.getByText(/Loading/)).toBeTruthy();

    forB.resolve({ items: [course(20, 'Course From B')], total: 1 });
    await waitFor(() => expect(screen.getByText('Course From B')).toBeTruthy());
    expect(screen.queryByText('Course From A')).toBeNull();
  });

  it('a stale error does not let the previous cluster´s titles paint', async () => {
    // `state.error` is shared by every dashboard fetch, so a failure that
    // happened elsewhere (e.g. PromptLibraryLayout's cluster fetch) can still
    // be set when this page mounts. That makes `coursesPending` false — the
    // `&& !coursesError` escape hatch that stops the loader spinning forever
    // on a real failure. The grid branch is checked before the error branch,
    // so without deriving the items from the marker, cluster A's titles paint
    // under cluster B's header.
    listClusters.mockResolvedValue({ items: [CLUSTER_A, CLUSTER_B], total: 2 });
    const forB = deferred();
    listCourses.mockReturnValueOnce(forB.promise);

    renderAt('/projects/23/clusters/2/courses', [
      setSelectedProject(PROJECT),
      setSelectedCluster(CLUSTER_A),
      fetchCoursesThunk.pending('r1', 1),
      fetchCoursesThunk.fulfilled({ items: [course(10, 'Course From A')], total: 1 }, 'r1', 1),
      // A failure from an unrelated fetch, still sitting in the shared field.
      fetchClustersThunk.pending('rX', 23),
      fetchClustersThunk.rejected(new Error('boom'), 'rX', 23, 'boom'),
    ]);

    expect(screen.queryByText('Course From A')).toBeNull();

    forB.resolve({ items: [course(20, 'Course From B')], total: 1 });
    await waitFor(() => expect(screen.getByText('Course From B')).toBeTruthy());
    expect(screen.queryByText('Course From A')).toBeNull();
  });

  it('the coursesLoadedFor marker gates the render, not just isLoadingCourses', () => {
    const store = configureStore({ reducer: { dashboard: dashboardReducer } });
    store.dispatch(setSelectedProject(PROJECT));
    store.dispatch(setSelectedCluster(CLUSTER_A));
    store.dispatch(fetchCoursesThunk.pending('r1', 1));
    store.dispatch(fetchCoursesThunk.fulfilled({ items: [course(10, 'Course From A')], total: 1 }, 'r1', 1));
    expect(store.getState().dashboard.coursesLoadedFor).toBe(1);

    // User navigates to cluster B: the pointer changes, clearing the marker
    // but NOT the stale items — CoursesPage must treat coursesLoadedFor !==
    // cid as still-pending rather than trusting isLoadingCourses alone.
    store.dispatch(setSelectedCluster(CLUSTER_B));
    const state = store.getState().dashboard;
    expect(state.coursesLoadedFor).toBeNull();
    expect(state.courses.items).toEqual([course(10, 'Course From A')]); // stale, still present
    expect(state.isLoadingCourses).toBe(false); // and isLoadingCourses alone would wrongly say "done"
  });
});

describe('CoursesPage — the empty state waits for an answer', () => {
  it('shows the loading line, not the empty state, before the list arrives', async () => {
    const courses = deferred();
    listCourses.mockReturnValue(courses.promise);

    renderAt('/projects/23/clusters/1/courses', [
      setSelectedProject(PROJECT),
      setSelectedCluster(CLUSTER_A),
    ]);
    await waitFor(() => expect(screen.getByText(/Loading/)).toBeTruthy());
    expect(emptyStatesRendered).toHaveLength(0);

    courses.resolve({ items: [course(10, 'Course A')], total: 1 });
    await waitFor(() => expect(screen.getByText('Course A')).toBeTruthy());
    expect(emptyStatesRendered).toHaveLength(0);
  });

  it('still shows the empty state for a cluster that really has none', async () => {
    listCourses.mockResolvedValue({ items: [], total: 0 });

    renderAt('/projects/23/clusters/1/courses', [
      setSelectedProject(PROJECT),
      setSelectedCluster(CLUSTER_A),
    ]);
    await waitFor(() => expect(emptyStatesRendered.length).toBeGreaterThan(0));
  });
});

describe('dashboardSlice — coursesLoadedFor follows the same requestId guard as clustersLoadedFor', () => {
  function reduce(actions) {
    const store = configureStore({ reducer: { dashboard: dashboardReducer } });
    actions.forEach((a) => store.dispatch(a));
    return store.getState().dashboard;
  }

  it('a superseded fulfilled cannot set the marker for the wrong cluster', () => {
    const state = reduce([
      fetchCoursesThunk.pending('request-A', 1),
      fetchCoursesThunk.pending('request-B', 2),
      fetchCoursesThunk.fulfilled({ items: [course(10, 'Wrong')], total: 1 }, 'request-A', 1),
    ]);
    expect(state.coursesLoadedFor).toBeNull();
    expect(state.isLoadingCourses).toBe(true);
  });

  it('records the cluster a list was actually fetched for', () => {
    const state = reduce([
      fetchCoursesThunk.pending('r1', 1),
      fetchCoursesThunk.fulfilled({ items: [course(10, 'A')], total: 1 }, 'r1', 1),
    ]);
    expect(state.coursesLoadedFor).toBe(1);
  });
});
