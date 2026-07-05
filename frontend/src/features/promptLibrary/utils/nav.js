import {
  canManagePrompts,
  canReadAllRequests,
  canReadAllReviews,
  canReadAudit,
  canReadLibrary,
} from './permissions';
import {
  plHome,
  plFlow,
  plRequests,
  plAdminRequests,
  plAdminReviews,
  plAdminAudit,
  plPromptNew,
  PL_BASE,
} from '../paths';

/** Intra-feature navigation (rendered inside the Prompt Library tab). */
export const MAIN_NAV = [
  { to: plHome, label: 'Library', end: true, visible: (u) => canReadLibrary(u) },
  // Flow view: any reader can see what each phase resolves and bind approved
  // prompts by reference (the approved-only rule for non-admins is server-enforced).
  { to: plFlow, label: 'Flow', visible: (u) => canReadLibrary(u) },
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
    className: 'btn btn-primary btn-header-action',
    visible: (u) => canManagePrompts(u),
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
