import { describe, expect, it } from 'vitest';
import {
  canManagePipelinePrompts,
  canManagePrompts,
  canReadAudit,
  canReadLibrary,
  isAdmin,
  roleLabel,
} from '../permissions';

const admin = { role: 'admin' };
const reviewer = { role: 'reviewer' };
const author = { role: 'author' };

describe('library gates', () => {
  it('any authenticated user reads the library', () => {
    expect(canReadLibrary(author)).toBe(true);
    expect(canReadLibrary(null)).toBe(false);
  });

  it('managers (admin + reviewer) manage library prompts', () => {
    expect(canManagePrompts(admin)).toBe(true);
    expect(canManagePrompts(reviewer)).toBe(true);
    expect(canManagePrompts(author)).toBe(false);
  });
});

describe('pipeline gate', () => {
  // Mirrors the backend's prompt.pipeline.edit: strictly admin. A reviewer
  // passing here would render editing controls the API 403s.
  it('is admin-only — reviewers are NOT pipeline managers', () => {
    expect(canManagePipelinePrompts(admin)).toBe(true);
    expect(canManagePipelinePrompts(reviewer)).toBe(false);
    expect(canManagePipelinePrompts(author)).toBe(false);
    expect(canManagePipelinePrompts(null)).toBe(false);
  });
});

describe('audit gate', () => {
  it('is admin-only', () => {
    expect(canReadAudit(admin)).toBe(true);
    expect(canReadAudit(reviewer)).toBe(false);
  });
});

describe('display helpers', () => {
  it('isAdmin checks the role string', () => {
    expect(isAdmin('admin')).toBe(true);
    expect(isAdmin('reviewer')).toBe(false);
  });

  it('roleLabel maps host roles to display names', () => {
    expect(roleLabel('admin')).toBe('Admin');
    expect(roleLabel('reviewer')).toBe('Lead');
    expect(roleLabel('author')).toBe('ID');
    expect(roleLabel('other')).toBe('User');
  });
});
