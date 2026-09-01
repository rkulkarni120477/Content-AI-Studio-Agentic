import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import ProtectedRoute from '@components/layout/ProtectedRoute/ProtectedRoute';
import WorkspaceLayout from '@components/layout/WorkspaceLayout/WorkspaceLayout';
import { ROUTES } from '@utils/constants';
import { Suspense } from 'react';
import Loader from '@components/common/Loader/Loader';
import { promptLibraryRoute } from '@features/promptLibrary/routes';
import { lazyWithReload } from '@utils/lazyWithReload';

const LoginPage     = lazyWithReload(() => import('@features/auth/pages/LoginPage/LoginPage'));
const TenantsPage     = lazyWithReload(() => import('@features/platform/pages/TenantsPage/TenantsPage'));
const TenantUsersPage = lazyWithReload(() => import('@features/platform/pages/TenantUsersPage/TenantUsersPage'));
const TenantRolesPage = lazyWithReload(() => import('@features/platform/pages/TenantRolesPage/TenantRolesPage'));
const ClustersPage  = lazyWithReload(() => import('@features/dashboard/pages/ClustersPage/ClustersPage'));
const CoursesPage   = lazyWithReload(() => import('@features/dashboard/pages/CoursesPage/CoursesPage'));
const ImportWizardPage = lazyWithReload(() => import('@features/import/pages/ImportWizardPage/ImportWizardPage'));
const CddPage       = lazyWithReload(() => import('@features/cdd/pages/CddPage/CddPage'));
const BlueprintPage = lazyWithReload(() => import('@features/blueprint/pages/BlueprintPage/BlueprintPage'));
const GeneratePage  = lazyWithReload(() => import('@features/generate/pages/GeneratePage/GeneratePage'));
const EditorPage    = lazyWithReload(() => import('@features/editor/pages/EditorPage/EditorPage'));
const FeedbackPage  = lazyWithReload(() => import('@features/feedback/pages/FeedbackPage/FeedbackPage'));
const WorkflowPage  = lazyWithReload(() => import('@features/workflow/pages/WorkflowPage/WorkflowPage'));
const ExportPage    = lazyWithReload(() => import('@features/export/pages/ExportPage/ExportPage'));
const SourceLibraryPage = lazyWithReload(() => import('@features/sourceLibrary/pages/SourceLibraryPage/SourceLibraryPage'));
const MetadataEditorPage = lazyWithReload(() => import('@features/sourceLibrary/pages/MetadataEditorPage/MetadataEditorPage'));
const StylePage     = lazyWithReload(() => import('@features/style/pages/StylePage/StylePage'));
const AnalyticsPage = lazyWithReload(() => import('@features/analytics/pages/AnalyticsPage/AnalyticsPage'));

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
      { path: 'sources/:jobId/metadata', element: wrap(<MetadataEditorPage />) },
      { path: 'style',     element: wrap(<StylePage />) },
      { path: 'cdd',       element: wrap(<CddPage />) },
      { path: 'blueprint', element: wrap(<BlueprintPage />) },
      { path: 'generate',  element: wrap(<GeneratePage />) },
      { path: 'editor',    element: wrap(<EditorPage />) },
      { path: 'feedback',  element: wrap(<FeedbackPage />) },
      { path: 'workflow',  element: wrap(<WorkflowPage />) },
      { path: 'export',    element: wrap(<ExportPage />) },
      { path: 'analytics', element: wrap(<AnalyticsPage />) },
    ],
  },

  { path: '*', element: <Navigate to={ROUTES.DASHBOARD} replace /> },
]);