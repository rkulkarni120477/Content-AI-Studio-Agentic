import { useLabels } from '@hooks/useLabels';
import styles from './GenerationTraceBar.module.scss';

export default function GenerationTraceBar({ promptName, promptVersion, cddLabel, blueprintLabel }) {
  const L = useLabels();
  return (
    <div className={styles.bar}>
      <span className={styles.bar__icon} aria-hidden>🔗</span>
      <span className={styles.bar__text}>
        <strong>Generation Trace:</strong>
        {' '}Prompt{' '}
        <code className={styles.pillPrompt}>
          {promptName || '—'} / {promptVersion || '—'}
        </code>
        {' · '}{L.cdd}{' '}
        <code className={styles.pillCdd}>{cddLabel || 'None'}</code>
        {' · '}{L.blueprint}{' '}
        <code className={styles.pillBp}>{blueprintLabel || 'None'}</code>
      </span>
    </div>
  );
}
