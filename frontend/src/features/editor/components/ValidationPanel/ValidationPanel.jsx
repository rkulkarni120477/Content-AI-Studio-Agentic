import styles from './ValidationPanel.module.scss';

export default function ValidationPanel({ result, compact = false }) {
  if (!result?.summary) return null;

  const { errors: nErr = 0, warnings: nWarn = 0, passed: nPass = 0, total_checks: total = 0 } = result.summary;
  const issues = [
    ...(result.errors || []).map((m) => ({ severity: 'error', message: m, location: '' })),
    ...(result.warnings || []).map((m) => ({ severity: 'warning', message: m, location: '' })),
    ...(result.blocks || []).flatMap((b) => [
      ...(b.errors || []).map((m) => ({ severity: 'error', message: m, location: b.block_label })),
      ...(b.warnings || []).map((m) => ({ severity: 'warning', message: m, location: b.block_label })),
    ]),
  ];

  if (compact) {
    if (nErr) return <span className={`${styles.compact} ${styles.compact__err}`}>❌ {nErr} error(s)</span>;
    if (nWarn) return <span className={`${styles.compact} ${styles.compact__warn}`}>⚠️ {nWarn} warning(s)</span>;
    return <span className={`${styles.compact} ${styles.compact__ok}`}>✅ Passed</span>;
  }

  return (
    <div className={styles.panel}>
      <h4 className={styles.panel__title}>🔍 Validation Results</h4>
      <div className={styles.metrics}>
        <div className={`${styles.metric} ${styles.metric__pass}`}>
          <span className={styles.metric__value}>✅ {nPass}</span>
          <span className={styles.metric__label}>Passed</span>
        </div>
        <div className={`${styles.metric} ${styles.metric__warn}`}>
          <span className={styles.metric__value}>⚠️ {nWarn}</span>
          <span className={styles.metric__label}>Warnings</span>
        </div>
        <div className={`${styles.metric} ${styles.metric__err}`}>
          <span className={styles.metric__value}>❌ {nErr}</span>
          <span className={styles.metric__label}>Errors</span>
        </div>
        <div className={`${styles.metric} ${styles.metric__total}`}>
          <span className={styles.metric__value}>{total}</span>
          <span className={styles.metric__label}>Checks</span>
        </div>
      </div>
      {issues.length > 0 && (
        <ul className={styles.issues}>
          {issues.map((issue, i) => (
            <li
              key={i}
              className={issue.severity === 'error' ? styles.issue__err : styles.issue__warn}
            >
              <strong>{issue.severity === 'error' ? '❌' : '⚠️'} {issue.message}</strong>
              {issue.location && <span className={styles.issue__loc}>📍 {issue.location}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
