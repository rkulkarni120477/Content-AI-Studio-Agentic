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
  modelChoice:      'GPT-5.4',
  expertDomain:     '',
  targetAudience:   '',
  audienceCategory: '',
  // Loading/error
  isLoadingProjects: false,
  isLoadingClusters: false,
  isLoadingCourses:  false,
  isLoadingModels:   false,
  error: null,
  // Project id whose cluster fetch has actually settled, or null.
  //
  // `clusters.items` being empty cannot tell "not loaded yet" from "this project
  // has no categories", and `isLoadingClusters` is false until the first pending
  // action lands — which is one paint after the component decides to fetch. A
  // page that only checks those two renders its empty state in the gap.
  clustersLoadedFor: null,
};

const dashboardSlice = createSlice({
  name: 'dashboard',
  initialState,
  reducers: {
    setSelectedProject(state, { payload }) {
      const prevId = state.selectedProject?.id;
      state.selectedProject = payload;
      if (!payload) {
        state.selectedCluster = null;
        state.selectedCourse  = null;
        state.clusters        = { items: [], total: 0 };
        state.courses         = { items: [], total: 0 };
        state.clustersLoadedFor = null;
        return;
      }
      if (prevId !== payload.id) {
        state.selectedCluster = null;
        state.selectedCourse  = null;
        state.clusters        = { items: [], total: 0 };
        state.courses         = { items: [], total: 0 };
        // Discarding the list without discarding the "it loaded" marker is what
        // let a wiped list read as an empty one. A project switch races its own
        // cluster fetch, so the marker has to go with the data.
        state.clustersLoadedFor = null;
      }
    },
    setSelectedCluster(state, { payload }) {
      state.selectedCluster = payload;
      state.selectedCourse  = null;
      state.courses         = { items: [], total: 0 };
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
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchProjectsThunk.pending,  (s) => { s.isLoadingProjects = true; s.error = null; })
      .addCase(fetchProjectsThunk.fulfilled, (s, { payload }) => { s.isLoadingProjects = false; s.projects = payload; })
      .addCase(fetchProjectsThunk.rejected,  (s, { payload }) => { s.isLoadingProjects = false; s.error = payload; })

      .addCase(fetchClustersThunk.pending,   (s) => { s.isLoadingClusters = true; s.error = null; })
      // `meta.arg` is the project id the list was fetched for. Recording it (not
      // a bare boolean) is what makes the marker survive StrictMode's double
      // dispatch and a project switch mid-flight: a list is only "loaded" for
      // the project it was actually fetched for.
      .addCase(fetchClustersThunk.fulfilled, (s, { payload, meta }) => {
        s.isLoadingClusters = false;
        s.clusters = payload;
        s.clustersLoadedFor = Number(meta.arg);
      })
      .addCase(fetchClustersThunk.rejected,  (s, { payload }) => { s.isLoadingClusters = false; s.error = payload; })

      .addCase(fetchCoursesThunk.pending,   (s) => { s.isLoadingCourses = true; s.error = null; })
      .addCase(fetchCoursesThunk.fulfilled, (s, { payload }) => { s.isLoadingCourses = false; s.courses = payload; })
      .addCase(fetchCoursesThunk.rejected,  (s, { payload }) => { s.isLoadingCourses = false; s.error = payload; })

      .addCase(fetchModelsThunk.fulfilled, (s, { payload }) => { s.models = payload; });
  },
});

export const {
  setSelectedProject, setSelectedCluster, setSelectedCourse,
  setModelChoice, setExpertDomain, setTargetAudience, setAudienceCategory,
  clearError, applyWorkspaceConfig,
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
export const selectDashboardError    = (s) => s.dashboard.error;
export const selectWorkspaceConfig = (s) => ({
  modelChoice:      s.dashboard.modelChoice,
  expertDomain:     s.dashboard.expertDomain,
  targetAudience:   s.dashboard.targetAudience,
  audienceCategory: s.dashboard.audienceCategory,
});
