import { useAuth } from '@hooks/useAuth';
import styles from './GettingStartedGuide.module.scss';

export default function GettingStartedGuide() {
  const { isAdmin, isReviewer, isAuthor } = useAuth();

  if (!isAdmin && !isReviewer && !isAuthor) return null;

  const steps = isAuthor
    ? [
        '1. Open your course workspace (CDD tab).',
        '2. Generate and pin a CDD.',
        '3. Create Blueprints per module.',
        '4. Launch generation, then edit blocks in Editor.',
        '5. Submit blocks for review via Workflow.',
      ]
    : [
        '1. Select Project → Category → Course.',
        '2. Configure Style and Document Registry.',
        '3. Generate CDD → Blueprint → Content.',
        '4. Review and approve in Workflow.',
        '5. Track usage in Analytics.',
      ];

  return (
    <details className={styles.guide}>
      <summary className={styles.guide__summary}>📖 Getting Started</summary>
      <ol className={styles.guide__list}>
        {steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </details>
  );
}
