import styles from './ExportTileButton.module.scss';

const ICONS = {
  md: '💾',
  json: '📦',
  html: '🌐',
  docx: '📄',
  xlsx: '📊',
  pdf: '📑',
};

export default function ExportTileButton({
  format,
  label,
  disabled,
  loading,
  onClick,
}) {
  const icon = ICONS[format] || '⬇️';
  const lines = (label || format.toUpperCase()).replace(/^[^\s]+\s/, '').trim();

  return (
    <button
      type="button"
      className={styles.tile}
      disabled={disabled || loading}
      onClick={onClick}
      aria-label={`Export ${format}`}
    >
      <span className={styles.tile__icon}>{icon}</span>
      <span className={styles.tile__label}>{lines || format.toUpperCase()}</span>
    </button>
  );
}
