import styles from './BackendDependencyNotice.module.scss';

export default function BackendDependencyNotice({ title, message, endpoints = [] }) {
  return (
    <div className={styles.notice} role="note">
      <div className={styles.notice__title}>{title}</div>
      <p>{message}</p>
      {endpoints.length > 0 && (
        <ul className={styles.notice__list}>
          {endpoints.map((ep) => (
            <li key={`${ep.method}-${ep.path}`}>
              <code>{ep.method} {ep.path}</code>
              {ep.purpose ? ` — ${ep.purpose}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
