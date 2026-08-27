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
