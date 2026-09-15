import { useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { flushDeferredToasts } from '@utils/deferredToast';
import IdentityBar from '@components/common/HeaderUser/IdentityBar';
import JobTracker from '@features/jobs/JobTracker';
import SelectionSidebar from './SelectionSidebar';
import styles from './SelectionLayout.module.scss';

export default function SelectionLayout({ sidebarProps, children }) {
  useEffect(() => { flushDeferredToasts(); }, []);

  return (
    <div className={styles.layout}>
      <JobTracker />
      <SelectionSidebar {...sidebarProps} />
      <main className={styles.main}>
        <IdentityBar />
        <div className={styles.mainBody}>{children}</div>
      </main>
      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
