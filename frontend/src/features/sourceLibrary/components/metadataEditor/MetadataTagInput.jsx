import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export default function MetadataTagInput({
  label,
  items = [],
  countLabel,
  addLabel = '+ Add...',
  tone = 'blue',
  onAdd,
  onRemove,
  maxItems,
  readOnly = false,
}) {
  const chipClass = tone === 'green' ? styles.chipGreen : styles.chip;

  return (
    <div className={styles.fieldBlock}>
      {(label || countLabel) ? (
        <div className={styles.fieldHeader}>
          {label ? <label className={styles.fieldLabel}>{label}</label> : <span />}
          {countLabel ? <span className={styles.fieldCount}>{countLabel}</span> : null}
        </div>
      ) : null}
      <div className={styles.chipWrap}>
        {items.map((item, idx) => {
          const text = typeof item === 'string' ? item : (item.label || item.job_id || '');
          const key = typeof item === 'string' ? `${text}-${idx}` : `${item.job_id || text}-${idx}`;
          return (
            <span key={key} className={chipClass}>
              {text}
              {!readOnly && (
                <button
                  type="button"
                  className={styles.chipRemove}
                  aria-label={`Remove ${text}`}
                  onClick={() => onRemove?.(idx)}
                >
                  ×
                </button>
              )}
            </span>
          );
        })}
        {!readOnly && (!maxItems || items.length < maxItems) && (
          <button type="button" className={styles.chipAdd} onClick={onAdd}>
            {addLabel}
          </button>
        )}
      </div>
    </div>
  );
}

MetadataTagInput.propTypes = {
  label: PropTypes.string.isRequired,
  items: PropTypes.array,
  countLabel: PropTypes.string,
  addLabel: PropTypes.string,
  tone: PropTypes.oneOf(['blue', 'green']),
  onAdd: PropTypes.func,
  onRemove: PropTypes.func,
  maxItems: PropTypes.number,
  readOnly: PropTypes.bool,
};
