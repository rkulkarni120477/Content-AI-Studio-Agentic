import {
  canManagePipelinePrompts,
  canReadAllRequests,
  canReadAllReviews,
  canReadAudit,
  canReadLibrary,
} from './permissions';
import {
  plHome,
  plCourses,
  plRequests,
  plAdminRequests,
  plAdminReviews,
  plAdminAudit,
  plPromptNew,
  PL_BASE,
} from '../paths';

/** Intra-feature navigation (rendered in the Prompts section sidebar). */
export const MAIN_NAV = [
  // The Library list surfaces only CAS pipeline prompts (Phase 12b) —
  // pipeline managers only. Readers browse the same facts via Flow/Courses.
  { to: plHome, label: 'Library', end: true, visible: (u) => canManagePipelinePrompts(u) },
  // Course-grouped view (requirements-doc §2), Phase 12c: also the lock
  // editor (absorbed from the retired Flow view). Any reader can see what
  // each course resolves and bind approved prompts by reference — the
  // approved-only rule for non-admins is server-enforced.
  { to: plCourses, label: 'Courses', visible: (u) => canReadLibrary(u) },
  // Regular users see their own requests; managers get the full admin queue below.
  { to: plRequests, label: 'Requests', visible: (u) => canReadLibrary(u) && !canReadAllRequests(u) },
  { to: plAdminRequests, label: 'Requests', visible: (u) => canReadAllRequests(u) },
  { to: plAdminReviews, label: 'Reviews', visible: (u) => canReadAllReviews(u) },
  { to: plAdminAudit, label: 'Audit Log', visible: (u) => canReadAudit(u) },
];

export const HEADER_ACTIONS = [
  {
    to: plPromptNew,
    label: '＋ New Prompt',
    // Freeform library authoring is retired from display (Phase 12b); the
    // form survives as the pipeline managers' draft → promote path.
    visible: (u) => canManagePipelinePrompts(u),
  },
];

/** Hide the "New Prompt" action while on a prompt form/detail page. */
export function visibleHeaderActions(user, pathname) {
  return HEADER_ACTIONS.filter((a) => {
    if (!a.visible(user)) return false;
    if (a.to === plPromptNew && pathname.startsWith(`${PL_BASE}/prompts/`)) return false;
    return true;
  });
}
