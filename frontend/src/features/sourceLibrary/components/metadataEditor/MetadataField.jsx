import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export default function MetadataField({ label, value, onChange, className }) {
  return (
    <div className={className}>
      <label className={styles.fieldLabel}>{label}</label>
      <input
        className={styles.input}
        value={value ?? ''}
        onChange={(e) => onChange?.(e.target.value)}
      />
    </div>
  );
}

MetadataField.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func,
  className: PropTypes.string,
};
