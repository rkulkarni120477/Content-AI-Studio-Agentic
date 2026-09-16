// @vitest-environment jsdom
/**
 * The Source Library must never call a document healthy without checking.
 *
 * It used to render `doc.status || 'processed'`, which meant a document with no
 * status displayed "processed". 246 of 612 AIM documents were unusable while the
 * library showed every one of them that way — so these tests pin the states that
 * matter, especially the two that must NOT read as success.
 */
import { render, screen, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';
import RetrievalStatus from '../RetrievalStatus';

const show = (doc) => render(<RetrievalStatus doc={doc} />);

// Without this every render stacks in the same document and the negative
// assertions below match a PREVIOUS test's output instead of this one's.
afterEach(cleanup);

describe('RetrievalStatus', () => {
  it('says Processing while the background ingest job is still running', () => {
    show({
      status: 'processing',
      total_units: 1,
      indexed_units: 0,
      extracted_chars: 18000,
      block: 'Block 2',
    });
    expect(screen.getByText('Processing…')).toBeTruthy();
    expect(screen.queryByText('Not indexed')).toBeNull();
    expect(screen.queryByText(/Searchable/)).toBeNull();
  });

  it('does not call a processing ebook empty just because preview chars are 0', () => {
    show({
      status: 'processing',
      total_units: 1,
      indexed_units: 0,
      extracted_chars: 0,
      block: 'Block 16',
    });
    expect(screen.getByText('Processing…')).toBeTruthy();
    expect(screen.queryByText('Nothing extracted')).toBeNull();
  });

  it('says Processing failed when the pipeline died', () => {
    show({ status: 'failed', total_units: 1, indexed_units: 0, block: 'Block 2' });
    expect(screen.getByText('Processing failed')).toBeTruthy();
  });

  it('reports a searchable document with its unit count', () => {
    show({ total_units: 36, indexed_units: 36, block: 'Block 2' });
    expect(screen.getByText(/Searchable · 36 units/)).toBeTruthy();
  });

  it('calls out a document that was extracted but never indexed', () => {
    // The Block 2 slide decks: 36 units extracted, 0 in the index, job "completed".
    show({ total_units: 36, indexed_units: 0, block: 'Block 2' });
    expect(screen.getByText('Not indexed')).toBeTruthy();
    expect(screen.getByText(/never reached the search index/)).toBeTruthy();
  });

  it('calls out a document that yielded no text at all', () => {
    // A legacy .doc, with antiword missing from the image.
    show({ total_units: 0, indexed_units: 0, block: 'Block 9' });
    expect(screen.getByText('Nothing extracted')).toBeTruthy();
  });

  it('does NOT report healthy when the index could not be reached', () => {
    show({ total_units: 36, block: 'Block 2' });   // indexed_units absent
    expect(screen.getByText('Unknown')).toBeTruthy();
    expect(screen.queryByText(/Searchable/)).toBeNull();
  });

  it('flags a searchable document that no block-wide generation will see', () => {
    // Both facts are true at once, and only showing the first is what hid 148 docs.
    show({ total_units: 5, indexed_units: 5, block: '', course_id: '101' });
    expect(screen.getByText(/Searchable · 5 units/)).toBeTruthy();
    expect(screen.getByText('No block')).toBeTruthy();
  });

  it('does not flag a global document for having no block', () => {
    show({ total_units: 5, indexed_units: 5, block: '', course_id: '-1' });
    expect(screen.queryByText('No block')).toBeNull();
  });

  it('reports partial indexing rather than rounding it up to searchable', () => {
    show({ total_units: 36, indexed_units: 12, block: 'Block 2' });
    expect(screen.getByText(/Partly searchable · 12\/36/)).toBeTruthy();
  });
});

/**
 * The empty-file case, which the first version could not detect.
 *
 * "Nothing extracted" keyed on total_units === 0. build_clean_content_document
 * falls back to a single unit holding reading_content when chunking yields
 * nothing, so a file the extractor could not read still records total_units = 1.
 * On the AIM index not one of 605 records has total_units = 0, while 109 of 120
 * legacy .doc records hold exactly one unit of zero characters — every one of
 * them displayed as an ordinary document.
 */
describe('a file that yielded no text', () => {

  const emptyDoc = {
    source_file_name: 'Landing Gear Systems Final Exam Match Statements.doc',
    total_units: 1,          // the fallback unit, not real content
    extracted_chars: 0,
    indexed_units: 0,
    block: 'Block 9',
  };

  it('says nothing was extracted, despite reporting one unit', () => {
    show(emptyDoc);
    expect(screen.getByText('Nothing extracted')).toBeTruthy();
    expect(screen.queryByText(/Searchable/)).toBeNull();
  });

  it('says so even when the search index cannot be reached', () => {
    // Definite beats unknown: whether text came out of the file is answerable
    // from the record alone, so a deployment with no index must not hide it.
    show({ ...emptyDoc, indexed_units: null });
    expect(screen.getByText('Nothing extracted')).toBeTruthy();
    expect(screen.queryByText('Unknown')).toBeNull();
  });

  it('does not condemn a record that predates the measurement', () => {
    // extracted_chars absent means "not measured", which is not "zero". Treating
    // the two alike would mark every document ingested before the field existed
    // as unusable.
    const { extracted_chars, ...older } = emptyDoc;
    show({ ...older, indexed_units: 4 });
    expect(screen.queryByText('Nothing extracted')).toBeNull();
  });

  it('still calls a real one-unit document searchable', () => {
    // Negative control: a genuine short document has one unit too, and the only
    // thing separating it from the .doc above is that text came out of it.
    show({ ...emptyDoc, extracted_chars: 1840, indexed_units: 1 });
    expect(screen.getByText(/Searchable/)).toBeTruthy();
    expect(screen.queryByText('Nothing extracted')).toBeNull();
  });
});
