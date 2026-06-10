import { useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { flushDeferredToasts } from '@utils/deferredToast';
import SelectionSidebar from './SelectionSidebar';
import styles from './SelectionLayout.module.scss';

export default function SelectionLayout({ sidebarProps, children }) {
  useEffect(() => { flushDeferredToasts(); }, []);

  return (
    <div className={styles.layout}>
      <SelectionSidebar {...sidebarProps} />
      <main className={styles.main}>{children}</main>
      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
