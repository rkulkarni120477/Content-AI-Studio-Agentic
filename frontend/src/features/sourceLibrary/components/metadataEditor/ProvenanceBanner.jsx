import PropTypes from 'prop-types';
import styles from '../../pages/MetadataEditorPage/MetadataEditorPage.module.scss';

export default function ProvenanceBanner({ children }) {
  return (
    <div className={styles.banner}>
      <span aria-hidden="true">✦</span>
      <span>{children}</span>
    </div>
  );
}

ProvenanceBanner.propTypes = {
  children: PropTypes.node.isRequired,
};
