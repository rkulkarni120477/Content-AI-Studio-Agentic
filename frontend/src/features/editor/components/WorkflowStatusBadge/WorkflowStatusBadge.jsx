import { WORKFLOW_STATE_ICONS, WORKFLOW_STATE_LABELS } from '@utils/constants';
import styles from './WorkflowStatusBadge.module.scss';

const PALETTE = {
  draft: styles.draft,
  in_review: styles.inReview,
  changes_requested: styles.changes,
  approved: styles.approved,
  published: styles.published,
  rejected: styles.rejected,
  archived: styles.archived,
};

export default function WorkflowStatusBadge({ state }) {
  const key = (state || 'draft').toLowerCase();
  const cls = PALETTE[key] || styles.draft;
  const icon = WORKFLOW_STATE_ICONS[key] || '•';
  const label = WORKFLOW_STATE_LABELS[key] || key.replace(/_/g, ' ');
  return (
    <span className={`${styles.badge} ${cls}`}>
      {icon} {label}
    </span>
  );
}
