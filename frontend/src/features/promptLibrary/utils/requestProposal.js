// A proposed prompt edit travels inside the request's free-text description
// using stable section markers. That keeps it human-readable everywhere the
// description is shown raw (exports, older clients) while the console parses
// it back for the admin's review-and-apply flow. No schema change needed.

export const SYSTEM_MARKER = '───── Proposed system prompt ─────';
export const USER_MARKER = '───── Proposed user prompt template ─────';

// sessionStorage key for the admin's "Apply this proposal in the editor"
// handoff (the editor reads and clears it on load). Not the URL — prompt
// bodies run to kilobytes.
export const APPLY_PROPOSAL_KEY = 'pl.applyProposal';

// Compose the stored description. Blocks are optional — pass null/undefined
// to omit one (e.g. the requester only changed the user template).
export function buildRequestDescription({ rationale, proposedSystem, proposedUser }) {
  const parts = [];
  if (rationale && rationale.trim()) parts.push(rationale.trim());
  if (proposedSystem != null) parts.push(`${SYSTEM_MARKER}\n${proposedSystem.trim()}`);
  if (proposedUser != null) parts.push(`${USER_MARKER}\n${proposedUser.trim()}`);
  return parts.join('\n\n');
}

export function hasProposal(text) {
  return Boolean(text) && (text.includes(SYSTEM_MARKER) || text.includes(USER_MARKER));
}

// Split a stored description back into rationale + proposal blocks.
// Plain descriptions (no markers) come back as { rationale, null, null }.
export function parseRequestDescription(text) {
  const raw = text || '';
  const sysAt = raw.indexOf(SYSTEM_MARKER);
  const usrAt = raw.indexOf(USER_MARKER);
  const firstMarker = [sysAt, usrAt].filter((i) => i !== -1).sort((a, b) => a - b)[0];
  if (firstMarker === undefined) {
    return { rationale: raw.trim(), proposedSystem: null, proposedUser: null };
  }
  const rationale = raw.slice(0, firstMarker).trim();
  let proposedSystem = null;
  let proposedUser = null;
  if (sysAt !== -1) {
    const start = sysAt + SYSTEM_MARKER.length;
    const end = usrAt > sysAt ? usrAt : raw.length;
    proposedSystem = raw.slice(start, end).trim();
  }
  if (usrAt !== -1) {
    const start = usrAt + USER_MARKER.length;
    const end = sysAt > usrAt ? sysAt : raw.length;
    proposedUser = raw.slice(start, end).trim();
  }
  return { rationale, proposedSystem, proposedUser };
}
