import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export default function DocumentContentPanel({
  overview = null,
  units = null,
  selectedUnit = null,
  sectionIndex = 0,
  sectionLoading = false,
  structureError = '',
  retagging = false,
  retagMessage = '',
  onRetag,
  isDocumentScope = false,
}) {
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
  const unitStatus = String(
    selectedUnit?.metadata?.tagging_status
    || units?.units?.[sectionIndex]?.tagging_status
    || '',
  ).toLowerCase();
  const unitNeedsRetry = unitStatus === 'failed' || unitStatus === 'pending';

  return (
    <div className={styles.contentPanel}>
      {isProcessing && (
        <div className={styles.processingBanner}>
          <span className={styles.processingWarn}>
            Background processing is still running. These sections are a preview —
            page units appear when extract, tagging, and indexing finish.
          </span>
        </div>
      )}
      {needsRetry > 0 && !isProcessing && (
        <div className={styles.taggingBanner}>
          <span className={styles.taggingWarn}>
            {needsRetry} page{needsRetry === 1 ? '' : 's'} need content tagging
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
              {`Section ${sectionIndex + 1}`}
              {selectedUnit.metadata?.page_number ? ` · p. ${selectedUnit.metadata.page_number}` : ''}
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
                disabled={retagging}
                onClick={() => onRetag?.({
                  unitIds: [
                    selectedUnit.unit_id
                    || selectedUnit.content_unit_id
                    || units?.units?.[sectionIndex]?.unit_id,
                  ].filter(Boolean),
                })}
              >
                {retagging ? 'Retrying…' : 'Retry this page'}
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
  retagMessage: PropTypes.string,
  onRetag: PropTypes.func,
  isDocumentScope: PropTypes.bool,
};
