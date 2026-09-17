import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

function isRetagLive(progress) {
  const state = String(progress?.state || '').toLowerCase();
  return state === 'starting' || state === 'running';
}

export default function DocumentContentPanel({
  overview = null,
  units = null,
  selectedUnit = null,
  sectionIndex = 0,
  sectionLoading = false,
  structureError = '',
  retagging = false,
  retagProgress = null,
  retagMessage = '',
  onRetag,
  isDocumentScope = false,
}) {
  const docType = String(
    overview?.document_type || selectedUnit?.metadata?.document_type || '',
  ).toLowerCase();
  const unitNoun = docType === 'course_calendar' ? 'section' : 'page';
  const unitNounPlural = docType === 'course_calendar' ? 'sections' : 'pages';
  const retryThisLabel = docType === 'course_calendar' ? 'Retry this section' : 'Retry this page';

  if (structureError) {
    return <div className={styles.error}>{structureError}</div>;
  }

  if (isDocumentScope) {
    return (
      <div className={styles.contentPanel}>
        <div className={styles.mutedNote}>
          Select a section on the left to view its raw content.
        </div>
        {(overview?.preview || '') && (
          <textarea
            className={styles.previewText}
            readOnly
            value={String(overview.preview || '').slice(0, 30000)}
          />
        )}
      </div>
    );
  }

  const failedCount = Number(overview?.tagging_failed_count ?? units?.tagging_failed_count ?? 0);
  const pendingCount = Number(overview?.tagging_pending_count ?? units?.tagging_pending_count ?? 0);
  const needsRetry = failedCount + pendingCount;
  const ingestStatus = String(overview?.status || '').toLowerCase();
  const isProcessing = ingestStatus === 'processing' || ingestStatus === 'pending';
  const live = isRetagLive(retagProgress) || retagging;
  const total = Number(retagProgress?.total || 0);
  const done = Number(retagProgress?.done || 0);
  const remaining = Number(
    retagProgress?.remaining != null
      ? retagProgress.remaining
      : Math.max(0, total - done),
  );
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const unitStatus = String(
    selectedUnit?.metadata?.tagging_status
    || units?.units?.[sectionIndex]?.tagging_status
    || '',
  ).toLowerCase();
  const unitNeedsRetry = unitStatus === 'failed' || unitStatus === 'pending';
  const sectionTitle = selectedUnit?.title
    || selectedUnit?.metadata?.sheet_name
    || (selectedUnit?.metadata?.page_number
      ? `p. ${selectedUnit.metadata.page_number}`
      : `Section ${sectionIndex + 1}`);

  return (
    <div className={styles.contentPanel}>
      {isProcessing && (
        <div className={styles.processingBanner}>
          <span className={styles.processingWarn}>
            Background processing is still running. These sections are a preview —
            content units appear when extract, tagging, and indexing finish.
          </span>
        </div>
      )}
      {live && !isProcessing && (
        <div className={styles.retagProgressBanner}>
          <div className={styles.retagProgressHeader}>
            <span className={styles.taggingWarn}>
              {total > 0
                ? `Tagging ${done} of ${total} ${unitNounPlural} · ${remaining} remaining`
                : 'Starting content tagging…'}
            </span>
            <button type="button" className={styles.contentButton} disabled>
              Tagging…
            </button>
          </div>
          <div className={styles.retagProgressTrack} aria-hidden="true">
            <div className={styles.retagProgressFill} style={{ width: `${pct}%` }} />
          </div>
          {retagProgress?.last_page != null && (
            <div className={styles.mutedNote}>
              Last {unitNoun} tagged: {String(retagProgress.last_page)}
            </div>
          )}
        </div>
      )}
      {needsRetry > 0 && !isProcessing && !live && (
        <div className={styles.taggingBanner}>
          <span className={styles.taggingWarn}>
            {needsRetry} {needsRetry === 1 ? unitNoun : unitNounPlural} need content tagging
            {failedCount ? ` (${failedCount} failed)` : ''}
            {pendingCount ? ` (${pendingCount} pending)` : ''}
          </span>
          <button
            type="button"
            className={styles.contentButton}
            disabled={retagging}
            onClick={() => onRetag?.({ allFailed: true })}
          >
            {retagging ? 'Retrying…' : 'Retry all failed'}
          </button>
        </div>
      )}
      {retagMessage && <div className={styles.mutedNote}>{retagMessage}</div>}

      {sectionLoading ? (
        <div className={styles.unitDetail}>
          <h4 className={styles.sectionTitle}>Loading section…</h4>
          <textarea className={styles.previewText} readOnly value="" />
        </div>
      ) : selectedUnit ? (
        <div className={styles.unitDetail}>
          <div className={styles.sectionNavHeader}>
            <h4 className={styles.sectionTitle}>
              {sectionTitle}
              {unitNeedsRetry && (
                <span className={`${styles.statusPill} ${styles.statusFailed}`}>
                  {selectedUnit.metadata?.tagging_status
                    || units?.units?.[sectionIndex]?.tagging_status}
                </span>
              )}
            </h4>
            {unitNeedsRetry && (
              <button
                type="button"
                className={styles.contentButtonSecondary}
                disabled={live || retagging}
                onClick={() => onRetag?.({
                  unitIds: [
                    selectedUnit.unit_id
                    || selectedUnit.content_unit_id
                    || units?.units?.[sectionIndex]?.unit_id,
                  ].filter(Boolean),
                })}
              >
                {retagging && !live ? 'Retrying…' : retryThisLabel}
              </button>
            )}
          </div>
          {(selectedUnit.metadata?.tagging_error
            || units?.units?.[sectionIndex]?.tagging_error) && (
            <div className={styles.mutedNote}>
              {selectedUnit.metadata?.tagging_error
                || units?.units?.[sectionIndex]?.tagging_error}
            </div>
          )}
          <textarea className={styles.previewText} readOnly value={selectedUnit.text || ''} />
        </div>
      ) : (
        <textarea
          className={styles.previewText}
          readOnly
          value={(overview?.preview || '').slice(0, 30000)}
        />
      )}
    </div>
  );
}

DocumentContentPanel.propTypes = {
  overview: PropTypes.object,
  units: PropTypes.object,
  selectedUnit: PropTypes.object,
  sectionIndex: PropTypes.number,
  sectionLoading: PropTypes.bool,
  structureError: PropTypes.string,
  retagging: PropTypes.bool,
  retagProgress: PropTypes.object,
  retagMessage: PropTypes.string,
  onRetag: PropTypes.func,
  isDocumentScope: PropTypes.bool,
};
