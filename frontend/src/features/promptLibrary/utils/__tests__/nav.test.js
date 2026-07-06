import { describe, expect, it } from 'vitest';
import { MAIN_NAV, visibleHeaderActions } from '../nav';
import { plAdminAudit, plAdminRequests, plAdminReviews, plCourses, plFlow, plRequests } from '../../paths';

const admin = { role: 'admin' };
const reviewer = { role: 'reviewer' };
const author = { role: 'author' };

function visibleNav(user) {
  return MAIN_NAV.filter((item) => item.visible(user)).map((item) => item.to);
}

describe('MAIN_NAV visibility', () => {
  it('authors see Library, Flow, and their own Requests — no admin surfaces', () => {
    const nav = visibleNav(author);
    expect(nav).toContain(plFlow);
    expect(nav).toContain(plRequests);
    expect(nav).not.toContain(plAdminRequests);
    expect(nav).not.toContain(plAdminReviews);
    expect(nav).not.toContain(plAdminAudit);
  });

  it('managers get the admin request queue instead of the personal one', () => {
    const nav = visibleNav(reviewer);
    expect(nav).toContain(plAdminRequests);
    expect(nav).not.toContain(plRequests);
  });

  it('audit is admin-only', () => {
    expect(visibleNav(admin)).toContain(plAdminAudit);
    expect(visibleNav(reviewer)).not.toContain(plAdminAudit);
  });

  it('the Flow view is visible to every library reader', () => {
    for (const user of [admin, reviewer, author]) {
      expect(visibleNav(user)).toContain(plFlow);
    }
    expect(visibleNav(null)).not.toContain(plFlow);
  });

  it('the Courses view shares the Flow audience — every reader, no anonymous', () => {
    for (const user of [admin, reviewer, author]) {
      expect(visibleNav(user)).toContain(plCourses);
    }
    expect(visibleNav(null)).not.toContain(plCourses);
  });
});

describe('visibleHeaderActions', () => {
  it('offers New Prompt to managers outside prompt pages', () => {
    expect(visibleHeaderActions(admin, '/prompt-library')).toHaveLength(1);
    expect(visibleHeaderActions(reviewer, '/prompt-library/requests')).toHaveLength(1);
  });

  it('hides New Prompt for authors', () => {
    expect(visibleHeaderActions(author, '/prompt-library')).toHaveLength(0);
  });

  it('hides New Prompt while on a prompt form or detail page', () => {
    expect(visibleHeaderActions(admin, '/prompt-library/prompts/12')).toHaveLength(0);
    expect(visibleHeaderActions(admin, '/prompt-library/prompts/new')).toHaveLength(0);
  });
});
