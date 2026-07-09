import { useAppSelector, useAppDispatch } from '@app/hooks';
import {
  selectUser,
  selectIsAuthenticated,
  selectUserRole,
  selectIsAdmin,
  selectIsReviewer,
  selectAuthLoading,
  selectIsPlatformAdmin,
  selectProjectId,
} from '@features/auth/authSlice';
import { logoutThunk } from '@features/auth/authThunks';
import { ROLES } from '@utils/constants';

export function useAuth() {
  const dispatch      = useAppDispatch();
  const user          = useAppSelector(selectUser);
  const isAuth        = useAppSelector(selectIsAuthenticated);
  const role          = useAppSelector(selectUserRole);
  const isAdmin           = useAppSelector(selectIsAdmin);
  const isReviewer        = useAppSelector(selectIsReviewer);
  const isPlatformAdmin   = useAppSelector(selectIsPlatformAdmin);
  const projectId         = useAppSelector(selectProjectId);
  const isLoading         = useAppSelector(selectAuthLoading);

  const isAuthor      = role === ROLES.AUTHOR;
  const canApprove    = isAdmin || isReviewer;
  const canGenerate   = Boolean(user);
  // User management is platform-admin only — a project-scoped admin (Director)
  // can no longer create/edit/toggle users, even within their own project.
  const canManageUsers = isPlatformAdmin;
  const canClearDb = isAdmin || (user?.permissions?.includes('system.clear_db') ?? false);

  function logout() {
    dispatch(logoutThunk());
  }

  function hasPermission(action) {
    if (isAdmin) return true;
    return user?.permissions?.includes(action) ?? false;
  }

  return {
    user,
    role,
    isAuth,
    isAdmin,
    isReviewer,
    isAuthor,
    isPlatformAdmin,
    projectId,
    canApprove,
    canGenerate,
    canManageUsers,
    canClearDb,
    isLoading,
    logout,
    hasPermission,
  };
}
