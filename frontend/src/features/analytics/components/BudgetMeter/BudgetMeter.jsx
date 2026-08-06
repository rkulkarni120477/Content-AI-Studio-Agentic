import Button from '@components/common/Button/Button';
import styles from './BudgetMeter.module.scss';

const SCOPE_LABELS = { project: 'Tenant', course: 'Title', user: 'User' };

// Same reserve-worst-case-then-reconcile spend the enforcement path uses
// (promptops_app/services/budget_service.py) — a block state here means the
// backend is genuinely rejecting calls, not just a UI warning.
export default function BudgetMeter({ policy, onEdit, onDelete }) {
  const {
    scope, scope_id: scopeId, period, limit_usd: limit, warn_threshold_pct: warnPct,
    current_spend_usd: spend, current_tokens: tokens,
  } = policy;
  const pct = limit > 0 ? Math.min(100, (spend / limit) * 100) : 0;
  const state = spend >= limit ? 'blocked' : pct >= warnPct ? 'warn' : 'ok';

  return (
    <div className={styles.meter}>
      <div className={styles.meter__header}>
        <span className={styles.meter__scope}>{SCOPE_LABELS[scope] || scope} <strong>{scopeId}</strong></span>
        <span className={styles.meter__period}>{period}</span>
      </div>
      <div className={styles.meter__track}>
        <div className={`${styles.meter__fill} ${styles[`meter__fill--${state}`]}`} style={{ width: `${pct}%` }} />
      </div>
      <div className={styles.meter__footer}>
        <span>${spend.toFixed(2)} / ${limit.toFixed(2)} ({pct.toFixed(0)}%)</span>
        {state === 'blocked' && <span className={styles['meter__badge--blocked']}>⛔ Blocked</span>}
        {state === 'warn' && <span className={styles['meter__badge--warn']}>⚠️ Warning</span>}
        {(onEdit || onDelete) && (
          <span className={styles.meter__actions}>
            {onEdit && <Button variant="ghost" size="sm" onClick={() => onEdit(policy)}>Edit</Button>}
            {onDelete && <Button variant="danger-ghost" size="sm" onClick={() => onDelete(policy)}>Delete</Button>}
          </span>
        )}
      </div>
      <div className={styles.meter__tokens}>{(tokens ?? 0).toLocaleString()} tokens used this period</div>
    </div>
  );
}
