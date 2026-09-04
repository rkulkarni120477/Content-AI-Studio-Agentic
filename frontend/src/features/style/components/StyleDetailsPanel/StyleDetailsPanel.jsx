import Loader from '@components/common/Loader/Loader';
import { useLabels } from '@hooks/useLabels';
import { applyTerminology } from '@config/tenantLabels';
import {
  parseStyleUnderstanding,
  styleUnderstandingText,
} from '@features/style/utils/styleUnderstanding';
import styles from './StyleDetailsPanel.module.scss';

function styleSlug(style) {
  return style?.style_key ?? style?.style_id ?? '';
}

export default function StyleDetailsPanel({
  style,
  loading = false,
  inModal = false,
  onPreviewDocument,
}) {
  const L = useLabels();

  if (loading) {
    return (
      <div className={inModal ? styles.inModal : styles.panel}>
        <div className={styles.center}><Loader size="lg" /></div>
      </div>
    );
  }

  if (!style) return null;

  const understanding = applyTerminology(styleUnderstandingText(style), L, ['style']);
  const { sections, preamble, rawFallback } = parseStyleUnderstanding(understanding);
  const refDocs = style.reference_documents || [];

  return (
    <div
      className={inModal ? styles.inModal : styles.panel}
      role="region"
      aria-label={`${L.style} details for ${style.name}`}
    >
      {!inModal && (
        <h3 className={styles.heading}>📋 {L.style} Details — {style.name}</h3>
      )}

      <div className={styles.block}>
        <h4 className={styles.blockTitle}><strong>{L.style} ID:</strong></h4>
        {styleSlug(style) ? (
          <code className={styles.metaCode}>{styleSlug(style)}</code>
        ) : (
          <p className={styles.empty}>—</p>
        )}
      </div>

      <div className={styles.block}>
        <h4 className={styles.blockTitle}><strong>Custom Instructions:</strong></h4>
        {style.custom_instructions?.trim() ? (
          <textarea
            className={styles.textarea}
            rows={4}
            readOnly
            value={style.custom_instructions || ''}
          />
        ) : (
          <p className={styles.empty}>No custom instructions.</p>
        )}
      </div>

      <div className={styles.block}>
        <h4 className={styles.blockTitle}>
          <strong>Reference Documents ({refDocs.length}):</strong>
        </h4>
        {refDocs.length > 0 ? (
          <ul className={styles.refList}>
            {refDocs.map((doc) => (
              <li key={doc.id} className={styles.refItem}>
                <button
                  type="button"
                  className={styles.refLink}
                  onClick={() => String(doc.source_type || '').startsWith('dis') ? null : onPreviewDocument?.(doc.id)}
                >
                  📄 {doc.name}
                </button>
                <span className={styles.refTag}>({doc.source_type || 'general'})</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.empty}>No reference documents linked.</p>
        )}
      </div>

      {style.understanding_status === 'stale' && (
        <p className={styles.stale}>
          ⚠️ Understanding is out of date — new files were added.
          Click <strong>Understand</strong> to regenerate.
        </p>
      )}

      <hr className={styles.divider} />

      <div className={styles.block}>
        <h4 className={styles.intelTitle}>🧠 {L.style} Intelligence Layer (stored understanding):</h4>

        {!understanding?.trim() ? (
          <p className={styles.empty}>
            No understanding generated yet. Click <strong>Understand</strong> to generate.
          </p>
        ) : rawFallback ? (
          <pre className={styles.rawBlock}>{preamble || understanding}</pre>
        ) : (
          <div className={styles.sections}>
            {sections.map((sec) => (
              <div
                key={sec.name}
                className={`${styles.section} ${styles[`section--${sec.tone}`]}`}
              >
                <div className={styles.sectionHead}>
                  {sec.icon} {sec.name}
                </div>
                <div className={styles.sectionBody}>
                  {sec.body || <span className={styles.empty}>—</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
