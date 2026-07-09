import { Navigate, useLocation } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import {
  selectIsAuthenticated,
  selectIsAuthChecked,
  selectUserRole,
  selectIsPlatformAdmin,
  selectProjectId,
} from '@features/auth/authSlice';
import Loader from '@components/common/Loader/Loader';
import { ROUTES } from '@utils/constants';

/**
 * Guards a route behind authentication.
 * - requiredRole: single role string or array of roles allowed
 * - platformAdminOnly: only platform admins may render this route — a regular
 *   user is bounced straight into their own project (or an "awaiting
 *   assignment" screen if they have no project yet), never the project dashboard
 * - redirectTo: where to send unauthorized users (defaults to /login)
 */
export default function ProtectedRoute({ children, requiredRole, platformAdminOnly = false, redirectTo = ROUTES.LOGIN }) {
  const isAuth       = useAppSelector(selectIsAuthenticated);
  const isAuthChecked = useAppSelector(selectIsAuthChecked);
  const userRole     = useAppSelector(selectUserRole);
  const isPlatformAdmin = useAppSelector(selectIsPlatformAdmin);
  const projectId    = useAppSelector(selectProjectId);
  const location     = useLocation();

  if (!isAuthChecked) {
    return <Loader size="xl" overlay />;
  }

  if (!isAuth) {
    return <Navigate to={redirectTo} state={{ from: location }} replace />;
  }

  if (platformAdminOnly && !isPlatformAdmin) {
    // A tenant user always has a project (membership); send them to their
    // clusters. Anything else falls back to login.
    const fallback = projectId ? ROUTES.PROJECT_CLUSTERS(projectId) : ROUTES.LOGIN;
    return <Navigate to={fallback} replace />;
  }

  if (requiredRole) {
    const allowed = Array.isArray(requiredRole) ? requiredRole : [requiredRole];
    if (!allowed.includes(userRole)) {
      return <Navigate to={ROUTES.DASHBOARD} replace />;
    }
  }

  return children;
}
