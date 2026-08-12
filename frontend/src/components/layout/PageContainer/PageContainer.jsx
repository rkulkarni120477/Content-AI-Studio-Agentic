import { cn } from '@utils/helpers';
import Header from '../Header/Header';
import styles from './PageContainer.module.scss';

export default function PageContainer({
  title,
  breadcrumbs,
  headerActions,
  hideHeaderUser = false,
  children,
  className,
  noPadding = false,
}) {
  return (
    <div className={cn(styles.page, className)}>
      <Header
        title={title}
        breadcrumbs={breadcrumbs}
        actions={headerActions}
        hideUser={hideHeaderUser}
      />
      <main className={cn(styles.page__content, noPadding && styles['page__content--noPad'])}>
        {children}
      </main>
    </div>
  );
}
