import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export default function DocumentSummaryPanel({ summary }) {
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
};
