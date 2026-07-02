import { useState } from 'react';
import { Outlet, useParams } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import Sidebar from '../Sidebar/Sidebar';
import styles from './AppLayout.module.scss';
import storage from '@utils/storage';
import { STORAGE_KEYS } from '@utils/constants';

export default function AppLayout() {
  const { courseId } = useParams();
  const [collapsed, setCollapsed] = useState(
    () => storage.get(STORAGE_KEYS.SIDEBAR_OPEN) === false,
  );

  function toggleSidebar() {
    setCollapsed((c) => {
      const next = !c;
      storage.set(STORAGE_KEYS.SIDEBAR_OPEN, !next);
      return next;
    });
  }

  return (
    <div className={styles.layout}>
      <Sidebar courseId={courseId} collapsed={collapsed} onToggle={toggleSidebar} />
      <div className={styles.layout__main}>
        <Outlet />
      </div>
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: { fontFamily: 'Inter, sans-serif', fontSize: '14px' },
        }}
      />
    </div>
  );
}
