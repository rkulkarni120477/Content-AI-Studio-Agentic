import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { selectIsAuthenticated, selectIsAuthChecked, selectIsPlatformAdmin } from '@features/auth/authSlice';
import Loader from '@components/common/Loader/Loader';
import SelectionLayout from '@components/layout/SelectionLayout/SelectionLayout';
import { ROUTES } from '@utils/constants';

export default function PlatformAdminGuard() {
  const isAuth          = useAppSelector(selectIsAuthenticated);
  const isAuthChecked   = useAppSelector(selectIsAuthChecked);
  const isPlatformAdmin = useAppSelector(selectIsPlatformAdmin);
  const location        = useLocation();

  if (!isAuthChecked)   return <Loader size="xl" overlay />;
  if (!isAuth)          return <Navigate to={ROUTES.LOGIN} state={{ from: location }} replace />;
  if (!isPlatformAdmin) return <Navigate to={ROUTES.DASHBOARD} replace />;

  return (
    <SelectionLayout sidebarProps={{ variant: 'platform' }}>
      <Outlet />
    </SelectionLayout>
  );
}
