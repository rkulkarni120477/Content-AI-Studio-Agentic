/**
 * AgentBuilderNav Component
 * Navigation menu for Agent Builder feature
 */

import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import styles from './AgentBuilderNav.module.scss';

export function AgentBuilderNav() {
  const { user } = useAppSelector((state) => state.auth);
  const location = useLocation();

  const isActive = (path: string) => location.pathname.startsWith(path);

  return (
    <nav className={styles.nav}>
      <div className={styles.nav__header}>
        <h2 className={styles.nav__title}>Agent Builder</h2>
        {user && (
          <div className={styles.nav__user}>
            <span className={styles.nav__username}>{user.email}</span>
            <span className={`${styles.nav__role} ${styles[`nav__role--${user.role}`]}`}>
              {user.role}
            </span>
          </div>
        )}
      </div>

      <div className={styles.nav__sections}>
        <div className={styles.nav__section}>
          <h3 className={styles.nav__sectionTitle}>Agents</h3>
          <ul className={styles.nav__list}>
            <li>
              <Link
                to="/agent-builder/agents"
                className={`${styles.nav__link} ${isActive('/agent-builder/agents') ? styles['nav__link--active'] : ''}`}
              >
                All Agents
              </Link>
            </li>
            <li>
              <Link
                to="/agent-builder/agents/create"
                className={`${styles.nav__link} ${isActive('/agent-builder/agents/create') ? styles['nav__link--active'] : ''}`}
              >
                Create Agent
              </Link>
            </li>
          </ul>
        </div>

        <div className={styles.nav__section}>
          <h3 className={styles.nav__sectionTitle}>Workflows</h3>
          <ul className={styles.nav__list}>
            <li>
              <Link
                to="/agent-builder/workflows"
                className={`${styles.nav__link} ${isActive('/agent-builder/workflows') ? styles['nav__link--active'] : ''}`}
              >
                All Workflows
              </Link>
            </li>
            <li>
              <Link
                to="/agent-builder/workflows/create"
                className={`${styles.nav__link} ${isActive('/agent-builder/workflows/create') ? styles['nav__link--active'] : ''}`}
              >
                Create Workflow
              </Link>
            </li>
          </ul>
        </div>

        <div className={styles.nav__section}>
          <h3 className={styles.nav__sectionTitle}>Results & Analytics</h3>
          <ul className={styles.nav__list}>
            <li>
              <Link
                to="/agent-builder/results/history"
                className={`${styles.nav__link} ${isActive('/agent-builder/results/history') ? styles['nav__link--active'] : ''}`}
              >
                Run History
              </Link>
            </li>
            <li>
              <Link
                to="/agent-builder/results/analytics"
                className={`${styles.nav__link} ${isActive('/agent-builder/results/analytics') ? styles['nav__link--active'] : ''}`}
              >
                Analytics
              </Link>
            </li>
            <li>
              <Link
                to="/agent-builder/budget"
                className={`${styles.nav__link} ${isActive('/agent-builder/budget') ? styles['nav__link--active'] : ''}`}
              >
                Budget & Costs
              </Link>
            </li>
          </ul>
        </div>
      </div>

      <div className={styles.nav__footer}>
        <a href="/logout" className={styles.nav__logout}>
          Logout
        </a>
      </div>
    </nav>
  );
}

export default AgentBuilderNav;
