import styles from './RetrievalStatus.module.scss';

/**
 * What generation can actually do with this document — not what the upload did.
 *
 * The Source Library used to render `doc.status || 'processed'`, so a document
 * with no status at all displayed the word "processed". That reassurance was
 * wrong often enough to matter: measured on the AIM corpus 2026-08-27, 246 of 612
 * documents (40%) were unusable — 119 produced no content at all (every legacy
 * .doc, because antiword is missing from the DIS image) and 127 held 5,841
 * extracted units the vector index never received (including every Block 2 slide
 * deck). All 612 ingestion jobs reported "completed", and the library showed all
 * of them as processed. Blocks were rebuilt for weeks against content nobody
 * could see was missing.
 *
 * So this reports the two things that actually decide whether a document can
 * reach a generation, and says plainly when it cannot:
 *
 *   indexed_units > 0             -> Searchable
 *   extracted > 0, indexed 0      -> Extracted but not indexed (recoverable — the
 *                                    text is still stored; re-indexing restores it)
 *   extracted === 0               -> Nothing extracted (needs re-ingestion)
 *   indexed_units == null         -> Unknown; the index could not be reached
 *
 * `indexed_units == null` deliberately does NOT render as healthy. Claiming
 * searchable without checking is the failure this component exists to end.
 */
export default function RetrievalStatus({ doc }) {
  const extracted = Number(doc?.total_units ?? 0);
  const indexed = doc?.indexed_units;
  const blockMissing = !String(doc?.block || '').trim() && String(doc?.course_id) !== '-1';

  let tone = 'ok';
  let label = 'Searchable';
  let detail = '';

  if (indexed === null || indexed === undefined) {
    tone = 'unknown';
    label = 'Unknown';
    detail = 'Search index could not be reached — this is not a healthy result.';
  } else if (extracted === 0) {
    tone = 'error';
    label = 'Nothing extracted';
    detail = 'No text was recovered from this file. No generation can use it until it is re-ingested.';
  } else if (Number(indexed) === 0) {
    tone = 'error';
    label = 'Not indexed';
    detail = `${extracted} unit${extracted === 1 ? '' : 's'} extracted but never reached the search index, so generation cannot see this document. The text is still stored — re-indexing restores it.`;
  } else if (Number(indexed) < extracted) {
    tone = 'warn';
    label = `Partly searchable · ${indexed}/${extracted}`;
    detail = 'Some extracted units are missing from the search index.';
  } else {
    label = `Searchable · ${indexed} unit${Number(indexed) === 1 ? '' : 's'}`;
  }

  return (
    <div className={styles.wrap}>
      <span className={`${styles.pill} ${styles[tone]}`} title={detail || undefined}>{label}</span>
      {/* Separate from the pill: a document can be perfectly searchable and still be
          excluded from every block-wide generation because it carries no block.
          That combination is exactly what hid 148 documents. */}
      {blockMissing && (
        <span
          className={`${styles.pill} ${styles.warn}`}
          title="Not linked to a block, so block-wide CDD and Blueprint generation will not include it."
        >
          No block
        </span>
      )}
      {detail && <div className={styles.detail}>{detail}</div>}
    </div>
  );
}
