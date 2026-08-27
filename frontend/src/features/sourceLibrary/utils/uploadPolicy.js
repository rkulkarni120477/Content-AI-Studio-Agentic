/**
 * Client-side mirror of the server's upload policy check.
 *
 * The server is authoritative (app/core/upload_formats.py, config/upload_formats.json)
 * and this only saves a round trip by refusing an unusable file before it is sent.
 * That makes a FALSE rejection the dangerous direction: the server never sees the
 * request, so a good file is blocked with no way to appeal. Hence the caution
 * below about an unknown policy, and hence this living in its own module with
 * tests rather than inline in a 900-line page.
 */

/** Lowercased extension, or '' when the name has none. Case- and path-insensitive. */
export function extensionOf(filename) {
  const name = String(filename || '').trim().toLowerCase();
  if (!name.includes('.')) return '';
  return name.slice(name.lastIndexOf('.') + 1);
}

/**
 * Why this file cannot be uploaded, or '' if it can.
 *
 * `policy` is the object served by GET /source-library/upload-policy. A null or
 * malformed policy returns '' — let the request through and let the server decide.
 * Guessing a rejection from a policy we could not read would block valid uploads
 * for a reason the user cannot act on.
 */
export function rejectionReason(policy, filename) {
  if (!policy) return '';
  const supported = policy.supported_extensions;
  const blocked = policy.blocked_extensions || {};
  if (!Array.isArray(supported) || supported.length === 0) return '';

  const ext = extensionOf(filename);
  // Config values are normalised server-side, but a hand-edited file could still
  // carry ".PDF" or "PDF" — compare on the same footing rather than trusting it.
  const norm = (e) => String(e || '').trim().toLowerCase().replace(/^\./, '');

  const blockedKey = Object.keys(blocked).find((k) => norm(k) === ext);
  if (blockedKey) return blocked[blockedKey];

  if (!supported.some((e) => norm(e) === ext)) {
    const list = supported.map((e) => `.${norm(e)}`).join(', ');
    return `.${ext || 'unknown'} is not a supported document type. Supported: ${list}.`;
  }
  return '';
}

/** `accept` attribute for the file picker, from the same list. */
export function acceptAttribute(policy) {
  const supported = policy?.supported_extensions;
  if (!Array.isArray(supported)) return '';
  return supported.map((e) => `.${String(e).trim().toLowerCase().replace(/^\./, '')}`).join(',');
}
