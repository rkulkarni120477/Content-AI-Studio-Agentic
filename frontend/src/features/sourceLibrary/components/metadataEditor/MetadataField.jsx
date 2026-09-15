import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export default function MetadataField({ label, value, onChange, className, readOnly = false }) {
  return (
    <div className={className}>
      <label className={styles.fieldLabel}>{label}</label>
      <input
        className={styles.input}
        value={value ?? ''}
        readOnly={readOnly}
        onChange={readOnly ? undefined : (e) => onChange?.(e.target.value)}
      />
    </div>
  );
}

MetadataField.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func,
  className: PropTypes.string,
  readOnly: PropTypes.bool,
};
