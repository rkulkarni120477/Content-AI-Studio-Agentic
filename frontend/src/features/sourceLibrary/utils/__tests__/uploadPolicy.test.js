/**
 * A file the pipeline can read must never be refused by the browser.
 *
 * This check runs before the request is sent, so a false rejection is final —
 * the server never sees it and the user has no way to appeal. Extensions arrive
 * in whatever case the operating system produced (Windows scanners routinely
 * emit .PDF, .DOCX), and matching those literally against a lowercase list would
 * refuse perfectly good documents with a message telling them PDFs are supported.
 */
import { describe, it, expect } from 'vitest';
import { extensionOf, rejectionReason, acceptAttribute } from '../uploadPolicy';

const POLICY = {
  supported_extensions: ['pdf', 'docx', 'pptx', 'xlsx'],
  blocked_extensions: { doc: 'Legacy Word is not accepted yet. Re-save as .docx.' },
};

describe('extensionOf', () => {
  it.each([
    ['report.pdf', 'pdf'],
    ['REPORT.PDF', 'pdf'],
    ['Block 9.Pdf', 'pdf'],
    ['Landing Gear Projects/PROJECT A27.DOCX', 'docx'],
    ['archive.tar.gz', 'gz'],
    ['READMEfile', ''],
    ['', ''],
  ])('%s -> %s', (name, ext) => expect(extensionOf(name)).toBe(ext));
});

describe('rejectionReason', () => {
  it('accepts a supported type in upper case', () => {
    expect(rejectionReason(POLICY, 'REPORT.PDF')).toBe('');
  });

  it('accepts a supported type in mixed case', () => {
    expect(rejectionReason(POLICY, 'Block 9 Syllabus.DocX')).toBe('');
  });

  it('still blocks a blocked type in upper case', () => {
    expect(rejectionReason(POLICY, 'PROJECT A27.DOC')).toContain('Re-save as .docx');
  });

  it('tolerates a hand-edited config whose entries carry dots or capitals', () => {
    const messy = { supported_extensions: ['.PDF', 'DocX'], blocked_extensions: { '.DOC': 'no' } };
    expect(rejectionReason(messy, 'a.pdf')).toBe('');
    expect(rejectionReason(messy, 'b.docx')).toBe('');
    expect(rejectionReason(messy, 'c.doc')).toBe('no');
  });

  it('refuses an unsupported type and says what is accepted', () => {
    const why = rejectionReason(POLICY, 'archive.zip');
    expect(why).toContain('.zip is not a supported document type');
    expect(why).toContain('.pdf');
  });

  it('lets an unknown policy through rather than guessing a rejection', () => {
    // The server still enforces it. Blocking here on a policy we could not read
    // would refuse valid files for a reason the user cannot act on.
    expect(rejectionReason(null, 'anything.xyz')).toBe('');
    expect(rejectionReason({}, 'anything.xyz')).toBe('');
    expect(rejectionReason({ supported_extensions: [] }, 'anything.xyz')).toBe('');
  });
});

describe('acceptAttribute', () => {
  it('builds the picker filter from the same list', () => {
    expect(acceptAttribute(POLICY)).toBe('.pdf,.docx,.pptx,.xlsx');
  });

  it('normalises dots and capitals so the attribute is always well formed', () => {
    expect(acceptAttribute({ supported_extensions: ['.PDF', 'DocX'] })).toBe('.pdf,.docx');
  });

  it('is empty when the policy is unknown, leaving the picker unfiltered', () => {
    expect(acceptAttribute(null)).toBe('');
  });
});
