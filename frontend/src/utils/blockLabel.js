/**
 * Infer a "Block N" label from free text.
 *
 * The block label is a RETRIEVAL KEY, not prose: the digest pipeline matches it
 * with SQL equality against `dis_course_calendars.block` and
 * `dis_content_units.metadata_json->>'block'`, and it keys the OpenSearch digest
 * store and the per-day digest cache. So it must be an exact literal, and a wrong
 * guess enumerates the wrong calendar (or nothing at all).
 *
 * That is why this only ever PREFILLS an editable field — the value the user sees
 * is the value that gets sent, and they can correct it. It never silently supplies
 * a label the user hasn't seen.
 *
 * Mirrors `app/core/dis_day_context.infer_block_label` / `_BLOCK_LABEL_RE`, which
 * in turn mirrors dis_backend's default `StructurePatternConfig.block_regex`.
 * Duplicated rather than shared because these are separate deployables — keep the
 * three in sync. Generic ("Block N" / "BLK N"), not tied to any client.
 */

// `\b(?:Block|BLK)\s*0*(\d+)\b`, case-insensitive — tolerates "BLK 02" and "block2".
const BLOCK_LABEL_RE = /\b(?:Block|BLK)\s*0*(\d+)\b/i;

/**
 * Return `"Block N"` from the first argument that contains a match, else `null`.
 *
 * Never guesses: a caller receiving `null` must treat it as "no prefill available"
 * and leave the field for the user, not fall back to some other label.
 *
 * @param {...(string|null|undefined)} texts Candidate sources, most authoritative first.
 * @returns {string|null}
 */
export function inferBlockLabel(...texts) {
  for (const text of texts) {
    if (!text || typeof text !== 'string') continue;
    const m = BLOCK_LABEL_RE.exec(text);
    // Normalise to canonical "Block N": strips leading zeros and collapses
    // whitespace/casing variants, so "BLK 02" and "block2" both key the same
    // calendar row as "Block 2". Matching the stored value is the whole point.
    if (m) return `Block ${parseInt(m[1], 10)}`;
  }
  return null;
}

export default inferBlockLabel;
