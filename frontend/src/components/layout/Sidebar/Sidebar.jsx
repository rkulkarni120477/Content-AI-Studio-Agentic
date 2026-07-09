import { NavLink, useNavigate } from 'react-router-dom';
import { useState } from 'react';
import { cn } from '@utils/helpers';
import { useAuth } from '@hooks/useAuth';
import { ROUTES, ROLES } from '@utils/constants';
import styles from './Sidebar.module.scss';

const NAV_ITEMS = [
  { label: 'Dashboard',    icon: '🏠', to: ROUTES.DASHBOARD,  roles: null },
  { label: 'Style',        icon: '🎨', to: ROUTES.STYLE,      roles: null },
  { label: 'Workflow',     icon: '⚙️',  to: ROUTES.WORKFLOW,   roles: null },
  { label: 'Prompts',      icon: '📝', to: ROUTES.PROMPTS,    roles: null },
  { label: 'Analytics',   icon: '📊', to: ROUTES.ANALYTICS,  roles: [ROLES.ADMIN, ROLES.REVIEWER] },
  { label: 'Prompt Library', icon: '📚', to: ROUTES.PROMPT_LIBRARY, roles: null },
];

const WORKSPACE_ITEMS = [
  { label: 'CDD',          icon: '📋', to: 'cdd' },
  { label: 'Blueprint',    icon: '🗺️', to: 'blueprint' },
  { label: 'Generate',     icon: '✨', to: 'generate' },
  { label: 'Editor',       icon: '✏️', to: 'editor' },
];

export default function Sidebar({ courseId, collapsed, onToggle }) {
  const { user, role, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  return (
    <aside className={cn(styles.sidebar, collapsed && styles['sidebar--collapsed'])} aria-label="Main navigation">
      {/* Logo */}
      <div className={styles.sidebar__logo}>
        <span className={styles.sidebar__logoIcon} aria-hidden="true">🎓</span>
        {!collapsed && <span className={styles.sidebar__logoText}>Content AI</span>}
      </div>

      {/* Toggle */}
      <button
        className={styles.sidebar__toggle}
        onClick={onToggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        type="button"
      >
        {collapsed ? '›' : '‹'}
      </button>

      <nav className={styles.sidebar__nav}>
        {/* Global Nav */}
        <ul className={styles.nav__list}>
          {NAV_ITEMS.filter((item) => !item.roles || item.roles.includes(role)).map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                className={({ isActive }) => cn(styles.nav__item, isActive && styles['nav__item--active'])}
                title={collapsed ? item.label : undefined}
              >
                <span className={styles.nav__icon} aria-hidden="true">{item.icon}</span>
                {!collapsed && <span className={styles.nav__label}>{item.label}</span>}
              </NavLink>
            </li>
          ))}
        </ul>

        {/* Workspace Nav (only when a course is selected) */}
        {courseId && (
          <>
            {!collapsed && <p className={styles.nav__section}>Workspace</p>}
            <ul className={styles.nav__list}>
              {WORKSPACE_ITEMS.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={`/workspace/${courseId}/${item.to}`}
                    className={({ isActive }) => cn(styles.nav__item, isActive && styles['nav__item--active'])}
                    title={collapsed ? item.label : undefined}
                  >
                    <span className={styles.nav__icon} aria-hidden="true">{item.icon}</span>
                    {!collapsed && <span className={styles.nav__label}>{item.label}</span>}
                  </NavLink>
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>

      {/* User Footer */}
      <div className={styles.sidebar__footer}>
        {!collapsed && (
          <div className={styles.user}>
            <span className={styles.user__avatar} aria-hidden="true">👤</span>
            <div className={styles.user__info}>
              <span className={styles.user__name}>{user?.username}</span>
              <span className={styles.user__role}>{role}</span>
            </div>
          </div>
        )}
        <button
          className={styles.sidebar__logout}
          onClick={handleLogout}
          title="Log out"
          type="button"
          aria-label="Log out"
        >
          🚪
        </button>
      </div>
    </aside>
  );
}
