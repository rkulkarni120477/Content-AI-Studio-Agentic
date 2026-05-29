import { Navigate, useLocation } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { selectIsAuthenticated, selectIsAuthChecked, selectUserRole } from '@features/auth/authSlice';
import Loader from '@components/common/Loader/Loader';
import { ROUTES } from '@utils/constants';

/**
 * Guards a route behind authentication.
 * - requiredRole: single role string or array of roles allowed
 * - redirectTo: where to send unauthorized users (defaults to /login)
 */
export default function ProtectedRoute({ children, requiredRole, redirectTo = ROUTES.LOGIN }) {
  const isAuth       = useAppSelector(selectIsAuthenticated);
  const isAuthChecked = useAppSelector(selectIsAuthChecked);
  const userRole     = useAppSelector(selectUserRole);
  const location     = useLocation();

  if (!isAuthChecked) {
    return <Loader size="xl" overlay />;
  }

  if (!isAuth) {
    return <Navigate to={redirectTo} state={{ from: location }} replace />;
  }

  if (requiredRole) {
    const allowed = Array.isArray(requiredRole) ? requiredRole : [requiredRole];
    if (!allowed.includes(userRole)) {
      return <Navigate to={ROUTES.DASHBOARD} replace />;
    }
  }

  return children;
}
