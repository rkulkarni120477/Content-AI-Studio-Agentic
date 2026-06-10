import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import ProtectedRoute from '@components/layout/ProtectedRoute/ProtectedRoute';
import WorkspaceLayout from '@components/layout/WorkspaceLayout/WorkspaceLayout';
import { ROUTES, ROLES } from '@utils/constants';
import { lazy, Suspense } from 'react';
import Loader from '@components/common/Loader/Loader';

const lazy$ = (factory) => {
  const Comp = lazy(factory);
  return (
    <Suspense fallback={<Loader size="xl" overlay />}>
      <Comp />
    </Suspense>
  );
};

const LoginPage     = lazy(() => import('@features/auth/pages/LoginPage/LoginPage'));
const ProjectsPage  = lazy(() => import('@features/dashboard/pages/ProjectsPage/ProjectsPage'));
const ClustersPage  = lazy(() => import('@features/dashboard/pages/ClustersPage/ClustersPage'));
const CoursesPage   = lazy(() => import('@features/dashboard/pages/CoursesPage/CoursesPage'));
const CddPage       = lazy(() => import('@features/cdd/pages/CddPage/CddPage'));
const BlueprintPage = lazy(() => import('@features/blueprint/pages/BlueprintPage/BlueprintPage'));
const GeneratePage  = lazy(() => import('@features/generate/pages/GeneratePage/GeneratePage'));
const EditorPage    = lazy(() => import('@features/editor/pages/EditorPage/EditorPage'));
const WorkflowPage  = lazy(() => import('@features/workflow/pages/WorkflowPage/WorkflowPage'));
const StylePage     = lazy(() => import('@features/style/pages/StylePage/StylePage'));
const AnalyticsPage = lazy(() => import('@features/analytics/pages/AnalyticsPage/AnalyticsPage'));
const CentralPage   = lazy(() => import('@features/central/pages/CentralPage/CentralPage'));

const wrap = (comp) => (
  <Suspense fallback={<Loader size="xl" overlay />}>
    {comp}
  </Suspense>
);

export const router = createBrowserRouter([
  { path: ROUTES.LOGIN, element: wrap(<LoginPage />) },

  {
    element: (
      <ProtectedRoute>
        <Outlet />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Navigate to={ROUTES.DASHBOARD} replace /> },
      { path: ROUTES.DASHBOARD, element: wrap(<ProjectsPage />) },
      { path: '/projects/:projectId/clusters', element: wrap(<ClustersPage />) },
      { path: '/projects/:projectId/clusters/:clusterId/courses', element: wrap(<CoursesPage />) },
      {
        path: ROUTES.CENTRAL,
        element: (
          <ProtectedRoute requiredRole={ROLES.ADMIN}>
            {wrap(<CentralPage />)}
          </ProtectedRoute>
        ),
      },
    ],
  },

  {
    path: '/workspace/:courseId',
    element: (
      <ProtectedRoute>
        <WorkspaceLayout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Navigate to="style" replace /> },
      { path: 'style',     element: wrap(<StylePage />) },
      { path: 'cdd',       element: wrap(<CddPage />) },
      { path: 'blueprint', element: wrap(<BlueprintPage />) },
      { path: 'generate',  element: wrap(<GeneratePage />) },
      { path: 'editor',    element: wrap(<EditorPage />) },
      { path: 'workflow',  element: wrap(<WorkflowPage />) },
      { path: 'analytics', element: wrap(<AnalyticsPage />) },
    ],
  },

  { path: '*', element: <Navigate to={ROUTES.DASHBOARD} replace /> },
]);