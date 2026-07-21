import { Suspense } from 'react';
import { Navigate } from 'react-router-dom';
import Loader from '@components/common/Loader/Loader';
import ProtectedRoute from '@components/layout/ProtectedRoute/ProtectedRoute';
import { ROLES } from '@utils/constants';
import { lazyWithReload } from '@utils/lazyWithReload';
import PromptLibraryLayout from './components/layout/PromptLibraryLayout';

const PromptListPage = lazyWithReload(() => import('./pages/PromptListPage'));
const PromptDetailPage = lazyWithReload(() => import('./pages/PromptDetailPage'));
const PromptFormPage = lazyWithReload(() => import('./pages/PromptFormPage'));
const CoursePromptsPage = lazyWithReload(() => import('./pages/CoursePromptsPage'));
const RequestsPage = lazyWithReload(() => import('./pages/RequestsPage'));
const RequestNewPage = lazyWithReload(() => import('./pages/RequestNewPage'));
const AdminRequestsPage = lazyWithReload(() => import('./pages/admin/AdminRequestsPage'));
const AdminRequestDetailPage = lazyWithReload(() => import('./pages/admin/AdminRequestDetailPage'));
const AdminReviewsPage = lazyWithReload(() => import('./pages/admin/AdminReviewsPage'));
const AdminAuditLogPage = lazyWithReload(() => import('./pages/admin/AdminAuditLogPage'));

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
    // Flow retired (Phase 12c) — its lock editor lives in the Courses view
    // now; old bookmarks land there.
    { path: 'flow', element: <Navigate to="/prompt-library/courses" replace /> },
    { path: 'courses', element: w(<CoursePromptsPage />) },
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
