import { Toaster } from 'react-hot-toast';
import SelectionSidebar from './SelectionSidebar';
import styles from './SelectionLayout.module.scss';

export default function SelectionLayout({ sidebarProps, children }) {
  return (
    <div className={styles.layout}>
      <SelectionSidebar {...sidebarProps} />
      <main className={styles.main}>{children}</main>
      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
