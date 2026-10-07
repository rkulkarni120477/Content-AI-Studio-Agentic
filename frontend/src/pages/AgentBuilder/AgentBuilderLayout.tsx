/**
 * AgentBuilderLayout
 * Main layout wrapper for all Agent Builder pages
 */

import React, { useState } from 'react';
import { Outlet } from 'react-router-dom';
import AgentBuilderNav from '@components/AgentBuilder/AgentBuilderNav';
import styles from './AgentBuilderLayout.module.scss';

export function AgentBuilderLayout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className={`${styles.layout} ${sidebarCollapsed ? styles['layout--collapsed'] : ''}`}>
      <aside className={styles.sidebar}>
        <button
          className={styles.sidebar__toggle}
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          aria-label="Toggle sidebar"
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {sidebarCollapsed ? '→' : '←'}
        </button>
        <AgentBuilderNav />
      </aside>

      <main className={styles.main}>
        <div className={styles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

export default AgentBuilderLayout;
