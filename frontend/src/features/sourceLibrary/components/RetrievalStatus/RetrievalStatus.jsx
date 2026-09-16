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
 *   status === processing|pending -> Background ingest still running (preview only)
 *   status === failed             -> Pipeline died before finalize
 *   extracted_chars === 0         -> Nothing extracted (needs re-ingestion)
 *   indexed_units > 0             -> Searchable
 *   units > 0, indexed 0          -> Extracted but not indexed (recoverable — the
 *                                    text is still stored; re-indexing restores it)
 *   indexed_units == null         -> Unknown; the index could not be reached
 *
 * `indexed_units == null` deliberately does NOT render as healthy. Claiming
 * searchable without checking is the failure this component exists to end.
 *
 * WHY extracted_chars AND NOT total_units
 * ---------------------------------------
 * The first version keyed "nothing extracted" on total_units === 0, and that
 * branch could never run. build_clean_content_document falls back to a single
 * unit holding reading_content when chunking yields nothing, so a file the
 * extractor could not read at all still records total_units = 1. Measured on the
 * AIM index: not one of 605 records has total_units = 0, while 109 of 120 legacy
 * .doc records have exactly one unit containing zero characters. The component
 * looked correct and reported "needs_review" for every unreadable file.
 *
 * "Nothing extracted" is also checked FIRST, ahead of the unknown case. It is
 * knowable from the record alone and needs no search index, so a deployment
 * whose index is unreachable should still say plainly that a file yielded no
 * text, rather than hiding a definite answer behind "Unknown".
 */
export default function RetrievalStatus({ doc }) {
  const units = Number(doc?.total_units ?? 0);
  const indexed = doc?.indexed_units;
  // null/undefined means the record predates the field, which is NOT the same as
  // zero — an older record simply was not measured, and calling that "nothing
  // extracted" would condemn every healthy document ingested before it existed.
  const chars = doc?.extracted_chars;
  const measured = chars !== null && chars !== undefined;
  const blockMissing = !String(doc?.block || '').trim() && String(doc?.course_id) !== '-1';
  const ingestStatus = String(doc?.status || '').toLowerCase();

  let tone = 'ok';
  let label = 'Searchable';
  let detail = '';

  if (ingestStatus === 'processing' || ingestStatus === 'pending') {
    tone = 'info';
    label = 'Processing…';
    detail = 'Background ingestion is still running (extract, page-tag, index). '
      + 'This row updates when the job finishes — the current preview is not the final book.';
  } else if (ingestStatus === 'failed') {
    tone = 'error';
    label = 'Processing failed';
    detail = String(doc?.error_message || '').trim()
      || 'Background ingestion failed. Re-upload the file or check DIS logs.';
  } else if (measured && Number(chars) === 0) {
    tone = 'error';
    label = 'Nothing extracted';
    detail = 'No text was recovered from this file, so no generation can use it. '
      + 'It needs re-ingesting with an extractor that can read this format.';
  } else if (indexed === null || indexed === undefined) {
    tone = 'unknown';
    label = 'Unknown';
    detail = 'Search index could not be reached — this is not a healthy result.';
  } else if (units === 0) {
    tone = 'error';
    label = 'Nothing extracted';
    detail = 'No text was recovered from this file. No generation can use it until it is re-ingested.';
  } else if (Number(indexed) === 0) {
    tone = 'error';
    label = 'Not indexed';
    detail = `${units} unit${units === 1 ? '' : 's'} extracted but never reached the search index, so generation cannot see this document. The text is still stored — re-indexing restores it.`;
  } else if (Number(indexed) < units) {
    tone = 'warn';
    label = `Partly searchable · ${indexed}/${units}`;
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
