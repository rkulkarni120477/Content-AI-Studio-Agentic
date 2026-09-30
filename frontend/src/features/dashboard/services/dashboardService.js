import { api } from '@services/apiClient';
import { PROJECTS, CLUSTERS, COURSES, MODELS, USERS, WORKSPACE } from '@services/endpoints';

export const dashboardService = {
  listProjects:    ()              => api.get(PROJECTS.LIST),
  getProject:      (id)           => api.get(PROJECTS.GET(id)),
  createProject:   (data)         => api.post(PROJECTS.CREATE, data),
  updateProject:   (id, data)     => api.put(PROJECTS.UPDATE(id), data),
  deleteProject:   (id)           => api.delete(PROJECTS.DELETE(id)),
  listProjectUsers:(id)           => api.get(PROJECTS.USERS(id)),
  assignProjectUser:(id, username)=> api.post(PROJECTS.USERS(id), { username }),
  unassignProjectUser:(id, user)  => api.delete(PROJECTS.UNASSIGN_USER(id, user)),

  listClusters:    (projectId)    => api.get(PROJECTS.CLUSTERS(projectId)),
  createCluster:   (projectId, data) => api.post(CLUSTERS.CREATE(projectId), data),
  updateCluster:   (id, data)     => api.put(CLUSTERS.UPDATE(id), data),
  deleteCluster:   (id)           => api.delete(CLUSTERS.DELETE(id)),

  listCourses:     (clusterId, { includeArchived = false } = {}) =>
    api.get(CLUSTERS.COURSES(clusterId), {
      params: includeArchived ? { include_archived: true } : undefined,
    }),
  createCourse:    (projectId, data) => api.post(PROJECTS.COURSES(projectId), data),
  getCourse:       (id)           => api.get(COURSES.GET(id)),
  updateCourse:    (id, data)     => api.put(COURSES.UPDATE(id), data),
  deleteCourse:    (id)           => api.delete(COURSES.DELETE(id)),
  restoreCourse:   (id)           => api.post(COURSES.RESTORE(id)),
  permanentlyDeleteCourse: (id)   => api.delete(COURSES.PERMANENT_DELETE(id)),
  listCourseUsers: (id)           => api.get(COURSES.USERS(id)),
  assignCourseUser:(id, username)=> api.post(COURSES.USERS(id), { username }),
  unassignCourseUser:(id, user)   => api.delete(COURSES.UNASSIGN_USER(id, user)),

  getWorkspace:    ()             => api.get(WORKSPACE.GET),
  updateWorkspaceConfig: (config) => api.put(WORKSPACE.UPDATE_CONFIG, config),
  listModels:      async ()       => {
    const res = await api.get(MODELS.LIST);
    return { items: res.models || [], default: res.default };
  },
  listUsers:       (projectId)    => api.get(USERS.LIST, projectId ? { params: { project_id: projectId } } : undefined),
};
