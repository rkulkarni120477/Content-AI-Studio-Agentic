import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { loginThunk, fetchMeThunk } from '@features/auth/authThunks';
import { authService } from '@features/auth/services/authService';
import {
  selectIsAuthenticated,
  selectIsAuthChecked,
  selectAuthLoading,
  selectAuthError,
  selectIsPlatformAdmin,
  selectProjectId,
  clearError,
  setToken,
  forceLogout,
} from '@features/auth/authSlice';
import { ROUTES } from '@utils/constants';
import Button from '@components/common/Button/Button';
import AppBrand from '@components/common/AppBrand/AppBrand';
import Loader from '@components/common/Loader/Loader';
import styles from './LoginPage.module.scss';

export default function LoginPage() {
  const dispatch        = useAppDispatch();
  const navigate        = useNavigate();
  const isAuth          = useAppSelector(selectIsAuthenticated);
  const isAuthChecked   = useAppSelector(selectIsAuthChecked);
  const isLoading       = useAppSelector(selectAuthLoading);
  const serverError     = useAppSelector(selectAuthError);
  const isPlatformAdmin = useAppSelector(selectIsPlatformAdmin);
  const projectId       = useAppSelector(selectProjectId);

  const [orgCode, setOrgCode]       = useState('');
  const [platformMode, setPlatformMode] = useState(false);
  const [username, setUsername]     = useState('');
  const [password, setPassword]     = useState('');
  const [config, setConfig]         = useState({ microsoft_enabled: false, local_login_enabled: true });
  const [urlError, setUrlError]     = useState(null);

  // Load public sign-in options.
  useEffect(() => {
    authService.config().then(setConfig).catch(() => {});
  }, []);

  // Handle the Microsoft OAuth return: backend redirects to /login?token=... or ?error=...
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');
    const error = params.get('error');
    if (error) {
      setUrlError(decodeURIComponent(error));
      window.history.replaceState({}, '', window.location.pathname);
      return;
    }
    if (token) {
      dispatch(setToken(token));
      dispatch(fetchMeThunk());
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, [dispatch]);

  // An authenticated user has a real destination only if they are a platform
  // admin (→ tenant dashboard) or belong to a project (→ their clusters). A
  // non-admin with no project has nowhere to go: /dashboard is platform-admin
  // only and bounces them straight back to /login, so navigating there creates
  // an infinite redirect loop (white screen + flickering loader). Such a user
  // must stay put and be told their account has no workspace.
  const hasDestination = isPlatformAdmin || Boolean(projectId);
  const stranded       = isAuth && !hasDestination;

  // Redirect once authenticated — by role, never by stale history.
  useEffect(() => {
    if (!isAuth) return;
    if (isPlatformAdmin) navigate(ROUTES.DASHBOARD, { replace: true });
    else if (projectId)  navigate(ROUTES.PROJECT_CLUSTERS(projectId), { replace: true });
    // else: authenticated but no workspace assigned — stay here (see `stranded`).
  }, [isAuth, isPlatformAdmin, projectId, navigate]);

  useEffect(() => () => { dispatch(clearError()); }, [dispatch]);

  function handleMicrosoft() {
    setUrlError(null);
    if (!orgCode.trim()) {
      setUrlError('Enter your organization code first.');
      return;
    }
    // Full-page redirect to the backend, which redirects to Microsoft.
    window.location.href = authService.microsoftLoginUrl(orgCode.trim().toLowerCase());
  }

  // Clear a stranded (no-workspace) session so the login form returns.
  function handleSignOut() {
    dispatch(forceLogout());
  }

  function handlePasswordLogin(e) {
    e.preventDefault();
    setUrlError(null);
    dispatch(loginThunk({
      username: username.trim(),
      password,
      organization_code: platformMode ? undefined : orgCode.trim().toLowerCase(),
      platform_admin: platformMode,
    }));
  }

  const error = urlError || serverError;

  // Hold the paint until the boot-time auth check settles, and while an already
  // authenticated user with a real destination is being redirected away —
  // otherwise the form flashes. A `stranded` user is authenticated but has
  // nowhere to route to, so fall through and show them the message below.
  if (!isAuthChecked || (isAuth && !stranded)) {
    return <Loader size="xl" overlay />;
  }

  if (stranded) {
    return (
      <div className={styles.page}>
        <aside className={styles.sidebar}>
          <div className={styles.brandRow}>
            <AppBrand />
          </div>
          <p className={styles.sidebar__label}>No workspace assigned</p>
          <div className={styles.error} role="alert">
            Your account isn’t a member of any organization yet. Ask an administrator
            to add you to a workspace, then sign in again.
          </div>
          <Button type="button" variant="secondary" size="lg" fullWidth onClick={handleSignOut}>
            Sign out
          </Button>
        </aside>
        <main className={styles.main}>
          <div className={styles.hero}>
            <AppBrand variant="hero" showTag={false} className={styles.hero__brand} />
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <aside className={styles.sidebar}>
        <div className={styles.brandRow}>
          <AppBrand />
        </div>
        <p className={styles.sidebar__label}>Sign in to continue</p>

        {error && <div className={styles.error} role="alert">{error}</div>}

        {/* Organization code + Microsoft */}
        <label className={styles.field}>
          Organization code
          <input
            type="text"
            autoFocus
            value={orgCode}
            onChange={(e) => setOrgCode(e.target.value)}
            className={styles.input}
            placeholder="e.g. academian"
            disabled={platformMode}
          />
        </label>

        <Button
          type="button"
          variant="secondary"
          size="lg"
          fullWidth
          onClick={handleMicrosoft}
          disabled={platformMode || !config.microsoft_enabled}
        >
          <span className={styles.msBtn}>
            <span className={styles.msLogo} aria-hidden="true" />
            Sign in with Microsoft
          </span>
        </Button>
        <p className={styles.hint}>
          {config.microsoft_enabled
            ? 'Uses your work account for this organization. The same Microsoft login can be used across orgs — pick the correct code above.'
            : 'Microsoft sign-in is not configured yet.'}
        </p>

        {config.local_login_enabled && (
          <>
            <div className={styles.divider}><span>or use username and password</span></div>

            <label className={styles.checkboxRow}>
              <input
                type="checkbox"
                checked={platformMode}
                onChange={(e) => setPlatformMode(e.target.checked)}
              />
              Platform administrator
            </label>

            <form onSubmit={handlePasswordLogin} className={styles.form} noValidate>
              <label className={styles.field}>
                Username
                <input
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className={styles.input}
                  placeholder="Enter username"
                />
              </label>
              <label className={styles.field}>
                Password
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={styles.input}
                  placeholder="Enter password"
                />
              </label>
              <Button type="submit" variant="primary" size="lg" fullWidth loading={isLoading}>
                Sign In
              </Button>
            </form>
          </>
        )}
      </aside>

      <main className={styles.main}>
        <div className={styles.hero}>
          <span className={styles.hero__badge}>Enterprise AI Platform</span>
          <AppBrand variant="hero" showTag={false} className={styles.hero__brand} />
          <p className={styles.hero__desc}>
            The centralised platform for AI-powered eLearning content creation.
            Manage prompts as code, enforce brand consistency, and generate production-ready courses at scale.
          </p>
        </div>
      </main>
    </div>
  );
}
