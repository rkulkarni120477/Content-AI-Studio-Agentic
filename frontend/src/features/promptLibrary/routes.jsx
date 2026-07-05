import { lazy, Suspense } from 'react';
import Loader from '@components/common/Loader/Loader';
import ProtectedRoute from '@components/layout/ProtectedRoute/ProtectedRoute';
import { ROLES } from '@utils/constants';
import PromptLibraryLayout from './components/layout/PromptLibraryLayout';

const PromptListPage = lazy(() => import('./pages/PromptListPage'));
const PromptDetailPage = lazy(() => import('./pages/PromptDetailPage'));
const PromptFormPage = lazy(() => import('./pages/PromptFormPage'));
const PipelineFlowPage = lazy(() => import('./pages/PipelineFlowPage'));
const RequestsPage = lazy(() => import('./pages/RequestsPage'));
const RequestNewPage = lazy(() => import('./pages/RequestNewPage'));
const AdminRequestsPage = lazy(() => import('./pages/admin/AdminRequestsPage'));
const AdminRequestDetailPage = lazy(() => import('./pages/admin/AdminRequestDetailPage'));
const AdminReviewsPage = lazy(() => import('./pages/admin/AdminReviewsPage'));
const AdminAuditLogPage = lazy(() => import('./pages/admin/AdminAuditLogPage'));

const w = (el) => <Suspense fallback={<Loader size="xl" overlay />}>{el}</Suspense>;
const MANAGER = [ROLES.ADMIN, ROLES.REVIEWER];

// Route subtree mounted at /prompt-library inside the host's ProtectedRoute group.
// Auth is already enforced by the ancestor ProtectedRoute; manager/admin pages add
// a role gate here as defence-in-depth (the backend enforces the same via RBAC).
export const promptLibraryRoute = {
  path: 'prompt-library',
  element: <PromptLibraryLayout />,
  children: [
    { index: true, element: w(<PromptListPage />) },
    {
      path: 'prompts/new',
      element: <ProtectedRoute requiredRole={MANAGER}>{w(<PromptFormPage />)}</ProtectedRoute>,
    },
    { path: 'prompts/:id', element: w(<PromptDetailPage />) },
    {
      path: 'prompts/:id/edit',
      element: <ProtectedRoute requiredRole={MANAGER}>{w(<PromptFormPage />)}</ProtectedRoute>,
    },
    { path: 'flow', element: w(<PipelineFlowPage />) },
    { path: 'requests', element: w(<RequestsPage />) },
    { path: 'requests/new', element: w(<RequestNewPage />) },
    {
      path: 'admin/requests',
      element: <ProtectedRoute requiredRole={MANAGER}>{w(<AdminRequestsPage />)}</ProtectedRoute>,
    },
    {
      path: 'admin/requests/:id',
      element: <ProtectedRoute requiredRole={MANAGER}>{w(<AdminRequestDetailPage />)}</ProtectedRoute>,
    },
    {
      path: 'admin/reviews',
      element: <ProtectedRoute requiredRole={MANAGER}>{w(<AdminReviewsPage />)}</ProtectedRoute>,
    },
    {
      path: 'admin/audit',
      element: <ProtectedRoute requiredRole={ROLES.ADMIN}>{w(<AdminAuditLogPage />)}</ProtectedRoute>,
    },
  ],
};
