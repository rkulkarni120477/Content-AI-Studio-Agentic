// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The real store wraps toast.success/error and rewrites every message with the
// tenant labels, so these spies stand in for react-hot-toast *underneath* that
// wrapper — they receive exactly what the user would see.
const shown = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('react-hot-toast', () => ({ default: { success: shown.success, error: shown.error } }));
vi.mock('../services/cddService', () => ({
  cddService: { importCdd: vi.fn() },
}));

import toast from 'react-hot-toast';
import store from '@app/store';
import { setSelectedProject } from '@features/dashboard/dashboardSlice';
import { cddService } from '../services/cddService';
import { flushDeferredToasts } from '@utils/deferredToast';
import { importCddThunk } from '../cddThunks';

// CAS-156: AIM renames CDD → "Blueprint" and Blueprint → "Outline". The CDD
// import toast read "Outline imported…" on AIM — first from using the Blueprint
// label, then (after that was fixed) from the store's toast wrapper rewriting
// the already-translated "Blueprint" into AIM's Blueprint label, "Outline".
const AIM_LABELS = { title: 'Block', cdd: 'Blueprint', blueprint: 'Outline' };

const importAs = async (uiLabels) => {
  store.dispatch(setSelectedProject({ id: 23, name: 'AIM', ui_labels: uiLabels }));
  await store.dispatch(importCddThunk({ projectId: 23 }));
};

describe('CDD import success toast as shown (CAS-156)', () => {
  beforeEach(() => {
    shown.success.mockReset();
    sessionStorage.clear();
    cddService.importCdd.mockReset();
    cddService.importCdd.mockResolvedValue({ id: 1 });
  });

  it('shows AIM its CDD label "Blueprint", not "Outline"', async () => {
    await importAs(AIM_LABELS);
    const message = shown.success.mock.calls[0][0];
    expect(message).toBe('Blueprint imported and set as active.');
    expect(message).not.toMatch(/outline/i);
  });

  it('shows AIM "Blueprint" in the deferred toast after the next page load', async () => {
    await importAs(AIM_LABELS);
    shown.success.mockReset();
    flushDeferredToasts();
    const message = shown.success.mock.calls[0][0];
    expect(message).toBe('Blueprint imported and pinned as active.');
    expect(message).not.toMatch(/outline/i);
  });

  it('says CDD for a tenant without renamed labels', async () => {
    await importAs({});
    expect(shown.success.mock.calls[0][0]).toBe('CDD imported and set as active.');
    shown.success.mockReset();
    flushDeferredToasts();
    expect(shown.success.mock.calls[0][0]).toBe('CDD imported and pinned as active.');
  });

  it('does not pass the localized flag on to react-hot-toast', async () => {
    await importAs(AIM_LABELS);
    expect(shown.success.mock.calls[0][1]).toEqual({});
  });

  it('still rewrites hardcoded default terms in other toasts', () => {
    store.dispatch(setSelectedProject({ id: 23, name: 'AIM', ui_labels: { style: 'Design' } }));
    toast.success('Style saved.');
    expect(shown.success.mock.calls[0][0]).toBe('Design saved.');
  });
});
