// @vitest-environment jsdom
// dashboardThunks -> authSlice reads localStorage at import time.
import { describe, expect, it } from 'vitest';

import reducer, {
  setSelectedProject, setSelectedCluster,
} from '@features/dashboard/dashboardSlice';
import { fetchCoursesThunk, fetchClustersThunk } from '@features/dashboard/dashboardThunks';

// Bug: after a hard refresh, CoursesPage/ClustersPage re-derive
// selectedProject/selectedCluster via their own sync effect (1-2 sequential
// API calls) while a separate effect fetches the real courses/clusters list
// in a single call. The list fetch usually wins the race and populates the
// list correctly — only for setSelectedProject/setSelectedCluster to fire
// moments later and wipe courses/clusters back to empty, rendering
// "No titles"/"No categories" for data that was already loaded right.
// These reducers must only ever touch the *selection pointer*
// (selectedCluster/selectedCourse) and the clustersLoadedFor/coursesLoadedFor
// markers, never the fetched lists — those are owned exclusively by
// fetchClustersThunk/fetchCoursesThunk's own pending/fulfilled cases.
// Clearing a marker is what makes a page show its loader instead of the
// previous project's/cluster's items; clearing the list is what used to
// wipe correctly-loaded data.

const baseState = {
  projects: { items: [], total: 0 },
  clusters: { items: [{ id: 1, name: 'Existing Cluster' }], total: 1 },
  courses: { items: [{ id: 10, name: 'Existing Course' }], total: 1 },
  models: { items: [], total: 0 },
  selectedProject: { id: 5, name: 'Project 5' },
  selectedCluster: { id: 1, name: 'Existing Cluster' },
  selectedCourse: null,
  modelChoice: 'GPT-5.4', expertDomain: '', targetAudience: '', audienceCategory: '',
  isLoadingProjects: false, isLoadingClusters: false, isLoadingCourses: false, isLoadingModels: false,
  error: null,
};

describe('dashboardSlice — selection reducers must not clobber independently-fetched lists', () => {
  it('setSelectedCluster leaves an already-loaded courses list intact', () => {
    const next = reducer(baseState, setSelectedCluster({ id: 1, name: 'Existing Cluster' }));
    expect(next.courses.items).toHaveLength(1);
    expect(next.courses).toBe(baseState.courses);
  });

  it('setSelectedProject (rehydrating the same project) leaves clusters/courses intact', () => {
    // The exact refresh scenario: selectedProject starts null, then gets set
    // to the id already encoded in the URL — a real *change* in prevId, but
    // not a genuine user-driven project switch.
    const state = { ...baseState, selectedProject: null };
    const next = reducer(state, setSelectedProject({ id: 5, name: 'Project 5' }));
    expect(next.clusters.items).toHaveLength(1);
    expect(next.courses.items).toHaveLength(1);
  });

  it('setSelectedProject to a genuinely different project still clears the stale selection pointer', () => {
    const next = reducer(baseState, setSelectedProject({ id: 6, name: 'Project 6' }));
    expect(next.selectedCluster).toBeNull();
    expect(next.selectedCourse).toBeNull();
  });

  it('setSelectedProject(null) still clears the selection pointer', () => {
    const next = reducer(baseState, setSelectedProject(null));
    expect(next.selectedProject).toBeNull();
    expect(next.selectedCluster).toBeNull();
    expect(next.selectedCourse).toBeNull();
  });

  it('setSelectedCluster still clears selectedCourse (a new cluster invalidates the old course pointer)', () => {
    const state = { ...baseState, selectedCourse: { id: 10, name: 'Existing Course' } };
    const next = reducer(state, setSelectedCluster({ id: 2, name: 'Different Cluster' }));
    expect(next.selectedCourse).toBeNull();
  });
});

// Bug: navigating cluster A -> cluster B in quick succession dispatches two
// fetchCoursesThunk(cid) calls. If A's response is slower (network jitter)
// and lands AFTER B's has already resolved and populated the list, A's
// stale "fulfilled" used to unconditionally overwrite state.courses with
// A's (wrong, or empty) list — the titles visibly vanish/revert until a
// manual reload re-fetches the right cluster. Reported live as "tiles
// disappear intermittently, reload brings them back."
describe('dashboardSlice — a superseded courses/clusters fetch cannot clobber a newer one', () => {
  it('a late-arriving stale fulfilled is ignored once a newer request has started', () => {
    let state = reducer(baseState, fetchCoursesThunk.pending('request-A', 1));
    state = reducer(state, fetchCoursesThunk.pending('request-B', 2));
    // A's response finally lands — after B already started, so it must not win.
    state = reducer(
      state,
      fetchCoursesThunk.fulfilled({ items: [{ id: 999, name: 'Wrong Cluster Course' }], total: 1 }, 'request-A', 1),
    );
    expect(state.courses).toBe(baseState.courses);   // untouched
    expect(state.isLoadingCourses).toBe(true);        // B is still in flight

    // B's own fulfilled, the current request, applies normally.
    state = reducer(
      state,
      fetchCoursesThunk.fulfilled({ items: [{ id: 20, name: 'Right Cluster Course' }], total: 1 }, 'request-B', 2),
    );
    expect(state.courses.items).toEqual([{ id: 20, name: 'Right Cluster Course' }]);
    expect(state.isLoadingCourses).toBe(false);
  });

  it('a late-arriving stale rejection does not overwrite a newer request´s error/loading state', () => {
    let state = reducer(baseState, fetchCoursesThunk.pending('request-A', 1));
    state = reducer(state, fetchCoursesThunk.pending('request-B', 2));
    state = reducer(state, fetchCoursesThunk.rejected(new Error('stale failure'), 'request-A', 1, 'stale error'));
    expect(state.error).toBeNull();
    expect(state.isLoadingCourses).toBe(true);
  });

  it('the same race guard applies to fetchClustersThunk', () => {
    let state = reducer(baseState, fetchClustersThunk.pending('request-A', 5));
    state = reducer(state, fetchClustersThunk.pending('request-B', 6));
    state = reducer(
      state,
      fetchClustersThunk.fulfilled({ items: [{ id: 999, name: 'Wrong Project Cluster' }], total: 1 }, 'request-A', 5),
    );
    expect(state.clusters).toBe(baseState.clusters);
    expect(state.isLoadingClusters).toBe(true);
  });

  it('when requests resolve in order, the normal single-fetch case is unaffected', () => {
    let state = reducer(baseState, fetchCoursesThunk.pending('request-A', 1));
    state = reducer(
      state,
      fetchCoursesThunk.fulfilled({ items: [{ id: 20, name: 'Course' }], total: 1 }, 'request-A', 1),
    );
    expect(state.courses.items).toEqual([{ id: 20, name: 'Course' }]);
    expect(state.isLoadingCourses).toBe(false);
  });
});
