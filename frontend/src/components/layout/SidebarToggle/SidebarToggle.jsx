import styles from './SidebarToggle.module.scss';

/** Collapse/expand chevron shared by every Studio sidebar. */
export default function SidebarToggle({ collapsed, onToggle }) {
  const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
  return (
    <button
      type="button"
      className={styles.toggle}
      onClick={onToggle}
      aria-label={label}
      aria-expanded={!collapsed}
      title={label}
    >
      {collapsed ? '»' : '«'}
    </button>
  );
}
