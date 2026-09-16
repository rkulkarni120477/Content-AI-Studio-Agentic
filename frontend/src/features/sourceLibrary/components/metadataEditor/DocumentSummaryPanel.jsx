import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export function sectionOptionLabel(unit, idx) {
  const st = String(unit?.tagging_status || '').toLowerCase();
  const mark = (st === 'failed' || st === 'pending') ? ` [${st}]` : '';
  if (unit?.page_number) return `p. ${unit.page_number}${mark}`;
  return `Section ${idx + 1}${mark}`;
}

export default function DocumentSummaryPanel({
  summary,
  units = [],
  selectedSectionId = 'document',
  onSectionChange,
  showSections = true,
}) {
  if (!summary) return null;
  const rows = [
    ['Type', summary.type],
    ['Size', summary.size],
    ['Pages', summary.pages || '—'],
    ['Purpose', summary.purpose],
  ];
  return (
    <div className={styles.leftColumn}>
      <div className={styles.card}>
        <div className={styles.cardBody}>
          <div className={styles.thumb} aria-hidden="true">📄</div>
          <div className={styles.docName}>{summary.name || summary.source_file_name}</div>
          <dl className={styles.metaList}>
            {rows.map(([label, value]) => (
              <div key={label} className={styles.metaRow}>
                <dt className={styles.metaLabel}>{label}</dt>
                <dd className={styles.metaValue}>{value || '—'}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      {showSections && (
        <div className={styles.sectionsCard}>
          <h4 className={styles.sectionTitle}>Sections</h4>
          <select
            className={styles.contentSelect}
            aria-label="Sections"
            value={selectedSectionId}
            onChange={(e) => onSectionChange?.(e.target.value)}
          >
            <option value="document">Whole document</option>
            {units.map((u, idx) => (
              <option key={u.unit_id || idx} value={String(u.unit_id)}>
                {sectionOptionLabel(u, idx)}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className={styles.objectMetaLink} title="Object Metadata is not available in this release">
        <div className={styles.objectMetaTitle}>Object Metadata</div>
        <div className={styles.objectMetaSub}>Available in a future release</div>
      </div>
    </div>
  );
}

DocumentSummaryPanel.propTypes = {
  summary: PropTypes.shape({
    name: PropTypes.string,
    source_file_name: PropTypes.string,
    type: PropTypes.string,
    size: PropTypes.string,
    pages: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    purpose: PropTypes.string,
  }),
  units: PropTypes.arrayOf(PropTypes.object),
  selectedSectionId: PropTypes.string,
  onSectionChange: PropTypes.func,
  showSections: PropTypes.bool,
};
