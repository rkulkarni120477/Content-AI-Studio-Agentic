// @vitest-environment jsdom
// dashboardThunks -> authSlice reads localStorage at import time.
import { describe, expect, it } from 'vitest';

import reducer, {
  setSelectedProject, setSelectedCluster,
} from '@features/dashboard/dashboardSlice';

// Bug: after a hard refresh, CoursesPage/ClustersPage re-derive
// selectedProject/selectedCluster via their own sync effect (1-2 sequential
// API calls) while a separate effect fetches the real courses/clusters list
// in a single call. The list fetch usually wins the race and populates the
// list correctly — only for setSelectedProject/setSelectedCluster to fire
// moments later and wipe courses/clusters back to empty, rendering
// "No titles"/"No categories" for data that was already loaded right.
// These reducers must only ever touch the *selection pointer*
// (selectedCluster/selectedCourse), never the fetched lists — those are
// owned exclusively by fetchClustersThunk/fetchCoursesThunk's own
// pending/fulfilled cases.

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
