import { createSlice } from '@reduxjs/toolkit';
import {
  fetchProjectsThunk, fetchClustersThunk, fetchCoursesThunk,
  fetchModelsThunk,
} from './dashboardThunks';

const initialState = {
  projects:        { items: [], total: 0 },
  clusters:        { items: [], total: 0 },
  courses:         { items: [], total: 0 },
  models:          { items: [], total: 0 },
  selectedProject: null,
  selectedCluster: null,
  selectedCourse:  null,
  // Sidebar generation context
    modelChoice:      'GPT-5.6 Terra',
  expertDomain:     '',
  targetAudience:   '',
  audienceCategory: '',
  // Loading/error
  isLoadingProjects: false,
  isLoadingClusters: false,
  isLoadingCourses:  false,
  isLoadingModels:   false,
  error: null,
  // Tracks the requestId of the most recently DISPATCHED courses/clusters
  // fetch, so a slower/out-of-order response from an earlier, superseded
  // fetch (e.g. the cluster-A request still in flight when the user has
  // already navigated to cluster B, whose own fetch resolves first) can't
  // win the race and clobber the list with the wrong project's/cluster's
  // items — or an empty one — once it finally lands. See fetchCoursesThunk's
  // and fetchClustersThunk's fulfilled/rejected below.
  coursesRequestId: null,
  clustersRequestId: null,
  // Project/cluster id whose clusters/courses fetch has actually settled, or
  // null. `clusters.items`/`courses.items` being empty cannot tell "not
  // loaded yet" from "this project/cluster has none", and isLoadingClusters/
  // isLoadingCourses is false until the first pending action lands — one
  // paint after the component decides to fetch. A page that only checks
  // those two can render its empty state in that gap. Set inside the
  // requestId-guarded fulfilled below, so a superseded response can't mark
  // the wrong project/cluster as loaded either.
  clustersLoadedFor: null,
  coursesLoadedFor: null,
};

const dashboardSlice = createSlice({
  name: 'dashboard',
  initialState,
  reducers: {
    setSelectedProject(state, { payload }) {
      // Only ever clears selectedCluster/selectedCourse and the *LoadedFor
      // markers — the "current selection pointer" metadata, which really is
      // stale once the project changes. Does NOT touch clusters/courses
      // items: those lists are owned exclusively by fetchClustersThunk/
      // fetchCoursesThunk's own pending/fulfilled reducers below, which
      // already track their real fetch lifecycle. This reducer firing here
      // too was a second, uncoordinated writer: on a hard refresh, ClustersPage/
      // CoursesPage's own sync effect re-derives selectedProject/selectedCluster
      // via 1-2 sequential API calls while a separate effect fetches the real
      // list in a single call — that fetch's `fulfilled` would win the race and
      // populate the list correctly, only for this reducer's reset to fire
      // moments later and wipe it back to empty, rendering "No titles"/"No
      // categories" for data that was already loaded right. Clearing the
      // *LoadedFor markers (not the lists) still lets the page's pending gate
      // correctly show a loader instead of the old project's stale items.
      const prevId = state.selectedProject?.id;
      state.selectedProject = payload;
      if (!payload || prevId !== payload.id) {
        state.selectedCluster = null;
        state.selectedCourse  = null;
        state.clustersLoadedFor = null;
        state.coursesLoadedFor  = null;
      }
    },
    setSelectedCluster(state, { payload }) {
      const prevId = state.selectedCluster?.id;
      state.selectedCluster = payload;
      state.selectedCourse  = null;
      if (!payload || prevId !== payload.id) {
        state.coursesLoadedFor = null;
      }
    },
    setSelectedCourse(state, { payload }) {
      state.selectedCourse = payload;
    },
    setModelChoice(state, { payload })      { state.modelChoice = payload; },
    setExpertDomain(state, { payload })     { state.expertDomain = payload; },
    setTargetAudience(state, { payload })   { state.targetAudience = payload; },
    setAudienceCategory(state, { payload }) { state.audienceCategory = payload; },
    clearError(state)                       { state.error = null; },
    applyWorkspaceConfig(state, { payload }) {
      if (payload.model_choice)      state.modelChoice      = payload.model_choice;
      if (payload.expert_domain)     state.expertDomain     = payload.expert_domain;
      if (payload.target_audience)   state.targetAudience   = payload.target_audience;
      if (payload.audience_category) state.audienceCategory = payload.audience_category;
    },
    patchSelectedProjectUiLabels(state, { payload }) {
      const { projectId, ui_labels } = payload || {};
      if (!projectId || !state.selectedProject || state.selectedProject.id !== projectId) return;
      state.selectedProject.ui_labels = ui_labels || {};
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchProjectsThunk.pending,  (s) => { s.isLoadingProjects = true; s.error = null; })
      .addCase(fetchProjectsThunk.fulfilled, (s, { payload }) => { s.isLoadingProjects = false; s.projects = payload; })
      .addCase(fetchProjectsThunk.rejected,  (s, { payload }) => { s.isLoadingProjects = false; s.error = payload; })

      .addCase(fetchClustersThunk.pending,   (s, action) => {
        s.clustersRequestId = action.meta.requestId;
        s.isLoadingClusters = true;
        s.error = null;
      })
      .addCase(fetchClustersThunk.fulfilled, (s, action) => {
        // A superseded request (the user already navigated on before this one
        // finished) must not overwrite what the newer, still-in-flight or
        // already-resolved request put there — including the *LoadedFor marker,
        // which `meta.arg` (the project id this list was actually fetched for)
        // keys on so a late response for the previous project can't mark the
        // new one loaded.
        if (action.meta.requestId !== s.clustersRequestId) return;
        s.isLoadingClusters = false;
        s.clusters = action.payload;
        s.clustersLoadedFor = Number(action.meta.arg);
      })
      .addCase(fetchClustersThunk.rejected,  (s, action) => {
        if (action.meta.requestId !== s.clustersRequestId) return;
        s.isLoadingClusters = false;
        s.error = action.payload;
      })

      .addCase(fetchCoursesThunk.pending,   (s, action) => {
        s.coursesRequestId = action.meta.requestId;
        s.isLoadingCourses = true;
        s.error = null;
      })
      .addCase(fetchCoursesThunk.fulfilled, (s, action) => {
        if (action.meta.requestId !== s.coursesRequestId) return;
        s.isLoadingCourses = false;
        s.courses = action.payload;
        s.coursesLoadedFor = Number(action.meta.arg);
      })
      .addCase(fetchCoursesThunk.rejected,  (s, action) => {
        if (action.meta.requestId !== s.coursesRequestId) return;
        s.isLoadingCourses = false;
        s.error = action.payload;
      })

      .addCase(fetchModelsThunk.fulfilled, (s, { payload }) => { s.models = payload; });
  },
});

export const {
  setSelectedProject, setSelectedCluster, setSelectedCourse,
  setModelChoice, setExpertDomain, setTargetAudience, setAudienceCategory,
  clearError, applyWorkspaceConfig, patchSelectedProjectUiLabels,
} = dashboardSlice.actions;

export default dashboardSlice.reducer;

export const selectProjects        = (s) => s.dashboard.projects;
export const selectClusters        = (s) => s.dashboard.clusters;
export const selectCourses         = (s) => s.dashboard.courses;
export const selectModels          = (s) => s.dashboard.models;
export const selectSelectedProject = (s) => s.dashboard.selectedProject;
export const selectSelectedCluster = (s) => s.dashboard.selectedCluster;
export const selectSelectedCourse  = (s) => s.dashboard.selectedCourse;
export const selectModelChoice      = (s) => s.dashboard.modelChoice;
export const selectExpertDomain     = (s) => s.dashboard.expertDomain;
export const selectTargetAudience   = (s) => s.dashboard.targetAudience;
export const selectAudienceCategory = (s) => s.dashboard.audienceCategory;
export const selectIsLoadingClusters = (s) => s.dashboard.isLoadingClusters;
export const selectClustersLoadedFor = (s) => s.dashboard.clustersLoadedFor;
export const selectIsLoadingCourses  = (s) => s.dashboard.isLoadingCourses;
export const selectCoursesLoadedFor  = (s) => s.dashboard.coursesLoadedFor;
export const selectDashboardError    = (s) => s.dashboard.error;
export const selectWorkspaceConfig = (s) => ({
  modelChoice:      s.dashboard.modelChoice,
  expertDomain:     s.dashboard.expertDomain,
  targetAudience:   s.dashboard.targetAudience,
  audienceCategory: s.dashboard.audienceCategory,
});
