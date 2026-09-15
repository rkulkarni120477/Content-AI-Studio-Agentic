import PropTypes from 'prop-types';
import Button from '@components/common/Button/Button';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export default function MetadataEditorFooter({
  onRevert,
  onCancel,
  onSave,
  saving,
  revertDisabled,
}) {
  return (
    <div className={styles.footer}>
      <button
        type="button"
        className={styles.revertBtn}
        onClick={onRevert}
        disabled={revertDisabled}
      >
        Revert to AI values
      </button>
      <div className={styles.footerActions}>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={onSave} loading={saving}>
          Save metadata
        </Button>
      </div>
    </div>
  );
}

MetadataEditorFooter.propTypes = {
  onRevert: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  saving: PropTypes.bool,
  revertDisabled: PropTypes.bool,
};
