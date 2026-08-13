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

/** "day 7 of 20" plus the elapsed clock, when either is known.
 *
 * A cold build holds one step label ("Building day digests...") for minutes, so
 * without these the line is identical whether the build is working or wedged. Days
 * come from the build itself; elapsed comes from the job's own created_at, so a
 * reattached page shows the true age of the build rather than time since the reload.
 */
function liveDetail(blockJob, block) {
  const parts = [];
  // Name the block when the running build is for a DIFFERENT one than the field shows.
  // /active matches on course + job type, and a course holds several blocks, so a page
  // showing "Block 3" can legitimately adopt a running "Block 2" build — saying so
  // beats reporting its completion as though it were this block's.
  const jobBlock = blockJob?.block;
  if (jobBlock && block && jobBlock.trim() !== block.trim()) {
    parts.push(`building ${jobBlock}`);
  }
  const days = blockJob?.days;
  if (days && days.total) {
    parts.push(`day ${days.done} of ${days.total}`);
    if (days.failed) parts.push(`${days.failed} failed`);
  }
  const startedAt = blockJob?.startedAt;
  if (startedAt) {
    const ms = Date.now() - new Date(startedAt).getTime();
    // Guard a clock skew between server and browser: a negative elapsed reads as a
    // build that started in the future.
    if (Number.isFinite(ms) && ms > 0) {
      const mins = Math.floor(ms / 60000);
      parts.push(mins >= 1 ? `${mins}m elapsed` : `${Math.floor(ms / 1000)}s elapsed`);
    }
  }
  return parts.length ? ` — ${parts.join(', ')}` : '';
}

function statusLine(blockJob, label, block) {
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
  return `⏳ ${currentStep || 'Working'}… (${status}${pct})${liveDetail(blockJob, block)}`;
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
  const line = statusLine(blockJob, label, block);
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
