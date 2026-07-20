import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import ProtectedRoute from '@components/layout/ProtectedRoute/ProtectedRoute';
import WorkspaceLayout from '@components/layout/WorkspaceLayout/WorkspaceLayout';
import { ROUTES } from '@utils/constants';
import { lazy, Suspense } from 'react';
import Loader from '@components/common/Loader/Loader';
import { promptLibraryRoute } from '@features/promptLibrary/routes';

const lazy$ = (factory) => {
  const Comp = lazy(factory);
  return (
    <Suspense fallback={<Loader size="xl" overlay />}>
      <Comp />
    </Suspense>
  );
};

const LoginPage     = lazy(() => import('@features/auth/pages/LoginPage/LoginPage'));
const TenantsPage     = lazy(() => import('@features/platform/pages/TenantsPage/TenantsPage'));
const TenantUsersPage = lazy(() => import('@features/platform/pages/TenantUsersPage/TenantUsersPage'));
const TenantRolesPage = lazy(() => import('@features/platform/pages/TenantRolesPage/TenantRolesPage'));
const ClustersPage  = lazy(() => import('@features/dashboard/pages/ClustersPage/ClustersPage'));
const CoursesPage   = lazy(() => import('@features/dashboard/pages/CoursesPage/CoursesPage'));
const ImportWizardPage = lazy(() => import('@features/import/pages/ImportWizardPage/ImportWizardPage'));
const CddPage       = lazy(() => import('@features/cdd/pages/CddPage/CddPage'));
const BlueprintPage = lazy(() => import('@features/blueprint/pages/BlueprintPage/BlueprintPage'));
const GeneratePage  = lazy(() => import('@features/generate/pages/GeneratePage/GeneratePage'));
const EditorPage    = lazy(() => import('@features/editor/pages/EditorPage/EditorPage'));
const WorkflowPage  = lazy(() => import('@features/workflow/pages/WorkflowPage/WorkflowPage'));
const ExportPage    = lazy(() => import('@features/export/pages/ExportPage/ExportPage'));
const SourceLibraryPage = lazy(() => import('@features/sourceLibrary/pages/SourceLibraryPage/SourceLibraryPage'));
const StylePage     = lazy(() => import('@features/style/pages/StylePage/StylePage'));
const AnalyticsPage = lazy(() => import('@features/analytics/pages/AnalyticsPage/AnalyticsPage'));

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
      {
        path: ROUTES.DASHBOARD,
        element: (
          <ProtectedRoute platformAdminOnly>
            {wrap(<TenantsPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: '/tenants/:tenantId/users',
        element: (
          <ProtectedRoute platformAdminOnly>
            {wrap(<TenantUsersPage />)}
          </ProtectedRoute>
        ),
      },
      {
        path: '/tenants/:tenantId/roles',
        element: (
          <ProtectedRoute platformAdminOnly>
            {wrap(<TenantRolesPage />)}
          </ProtectedRoute>
        ),
      },
      { path: '/projects/:projectId/clusters', element: wrap(<ClustersPage />) },
      { path: '/projects/:projectId/clusters/:clusterId/courses', element: wrap(<CoursesPage />) },
      { path: '/projects/:projectId/import', element: wrap(<ImportWizardPage />) },
      promptLibraryRoute,
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
      { index: true, element: <Navigate to="sources" replace /> },
      { path: 'sources',   element: wrap(<SourceLibraryPage />) },
      { path: 'style',     element: wrap(<StylePage />) },
      { path: 'cdd',       element: wrap(<CddPage />) },
      { path: 'blueprint', element: wrap(<BlueprintPage />) },
      { path: 'generate',  element: wrap(<GeneratePage />) },
      { path: 'editor',    element: wrap(<EditorPage />) },
      { path: 'workflow',  element: wrap(<WorkflowPage />) },
      { path: 'export',    element: wrap(<ExportPage />) },
      { path: 'analytics', element: wrap(<AnalyticsPage />) },
    ],
  },

  { path: '*', element: <Navigate to={ROUTES.DASHBOARD} replace /> },
]);