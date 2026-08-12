import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import { isTerminalJobStatus, JOB_STATUSES } from '@utils/constants';
import styles from './BlockWidePanel.module.scss';

const TIER_OPTIONS = [
  { value: 'draft', label: 'Draft (fastest)' },
  { value: 'standard', label: 'Standard' },
  { value: 'premium', label: 'Premium (best)' },
];

function statusLine(blockJob, label) {
  if (!blockJob) return null;
  const { status, currentStep, progress, warning } = blockJob;
  if (status === JOB_STATUSES.FAILED) return `❌ ${label} generation failed — see the error above.`;
  if (status === JOB_STATUSES.CANCELLED) return `⚠️ ${label} generation was cancelled.`;
  if (status === JOB_STATUSES.COMPLETED) {
    // A partially-extracted block is still worth keeping, but reporting a bare
    // "Done" hides real gaps: the failed rows are filled with defaults, not left
    // blank, so the document looks finished either way.
    return warning
      ? `⚠️ Done, with gaps — pinned as active. ${warning}`
      : '✅ Done — pinned as active.';
  }
  const pct = progress ? `, ${progress}%` : '';
  return `⏳ ${currentStep || 'Working'}… (${status}${pct})`;
}

/**
 * Shared block-wide (digest-pipeline) generation control for CDD and Blueprint.
 * Presentational + controlled — all job state/dispatch lives in the feature
 * slice; this only renders inputs and the live status line. Consolidates what
 * were two near-identical inline panels (and centralizes the SectionBadge fix).
 */
export default function BlockWidePanel({
  label, hint, block, onBlockChange, qualityTier, onQualityTierChange,
  isGenerating, blockJob, onGenerate,
}) {
  const line = statusLine(blockJob, label);
  const running = Boolean(blockJob && !isTerminalJobStatus(blockJob.status));
  return (
    <div className={styles.blockWide}>
      <SectionBadge title={`🧩 Block-wide ${label} (digest pipeline)`} />
      <p className={styles.blockWide__hint}>{hint}</p>
      <div className={styles.blockWide__row}>
        <Input
          label="Block"
          placeholder='e.g. "Block 2"'
          value={block}
          onChange={(e) => onBlockChange(e.target.value)}
          wrapperClassName={styles.blockWide__block}
        />
        <Select
          label="Quality"
          value={qualityTier}
          onChange={(e) => onQualityTierChange(e.target.value)}
          options={TIER_OPTIONS}
        />
        <Button
          variant="primary"
          size="md"
          loading={isGenerating}
          disabled={isGenerating || !block.trim()}
          onClick={onGenerate}
        >
          🧩 Generate Block {label}
        </Button>
      </div>
      {line && (
        <p className={styles.blockWide__status} role="status" aria-live="polite" aria-busy={running}>
          {line}
        </p>
      )}
    </div>
  );
}
