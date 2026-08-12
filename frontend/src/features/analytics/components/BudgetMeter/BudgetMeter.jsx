import Button from '@components/common/Button/Button';
import styles from './BudgetMeter.module.scss';

const SCOPE_LABELS = { project: 'Tenant', course: 'Title', user: 'User' };

const fmtUsd = (n) => `$${(n ?? 0).toFixed(2)}`;
const fmtTokens = (n) => (n ?? 0).toLocaleString();

// Same reserve-worst-case-then-reconcile spend the enforcement path uses
// (promptops_app/services/budget_service.py) — a block state here means the
// backend is genuinely rejecting calls, not just a UI warning.
//
// A policy caps EITHER usd OR tokens (limit_type) — but actual cost and
// token consumption are always tracked on the row regardless of which one
// is enforced, so both are always shown here (AC6), only the capped side
// drives the bar/blocked-warn state.
export default function BudgetMeter({ policy, onEdit, onDelete }) {
  const {
    scope, scope_id: scopeId, period, limit_type: limitType = 'usd',
    limit_usd: limitUsd, limit_tokens: limitTokens, warn_threshold_pct: warnPct,
    current_spend_usd: spend, current_tokens: tokens,
  } = policy;

  const isTokenCap = limitType === 'tokens';
  const limit = isTokenCap ? (limitTokens ?? 0) : (limitUsd ?? 0);
  const consumed = isTokenCap ? (tokens ?? 0) : (spend ?? 0);
  const remaining = limit - consumed;
  const pct = limit > 0 ? Math.min(100, (consumed / limit) * 100) : 0;
  const state = limit > 0 && consumed >= limit ? 'blocked' : pct >= warnPct ? 'warn' : 'ok';

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
        <span>
          {isTokenCap
            ? `${fmtTokens(consumed)} / ${fmtTokens(limit)} tokens (${pct.toFixed(0)}%)`
            : `${fmtUsd(consumed)} / ${fmtUsd(limit)} (${pct.toFixed(0)}%)`}
        </span>
        {state === 'blocked' && <span className={styles['meter__badge--blocked']}>⛔ Blocked</span>}
        {state === 'warn' && <span className={styles['meter__badge--warn']}>⚠️ Warning</span>}
        {(onEdit || onDelete) && (
          <span className={styles.meter__actions}>
            {onEdit && <Button variant="ghost" size="sm" onClick={() => onEdit(policy)}>Edit</Button>}
            {onDelete && <Button variant="danger-ghost" size="sm" onClick={() => onDelete(policy)}>Delete</Button>}
          </span>
        )}
      </div>
      <div className={styles.meter__details}>
        <span>Remaining: {isTokenCap ? `${fmtTokens(remaining)} tokens` : fmtUsd(remaining)}</span>
        <span>Actual cost: {fmtUsd(spend)}</span>
        <span>Tokens used: {fmtTokens(tokens)}</span>
      </div>
    </div>
  );
}
