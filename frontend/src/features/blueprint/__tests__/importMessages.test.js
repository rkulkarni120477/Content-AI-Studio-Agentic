// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The real store wraps toast.success/error and rewrites every message with the
// tenant labels, so these spies stand in for react-hot-toast *underneath* that
// wrapper — they receive exactly what the user would see.
const shown = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('react-hot-toast', () => {
  const toast = vi.fn();   // plain toast() — used for import warnings
  toast.success = shown.success;
  toast.error = shown.error;
  return { default: toast };
});
vi.mock('../services/blueprintService', () => ({
  blueprintService: {
    getJobStatus: vi.fn(), getBlueprint: vi.fn(), importBlueprint: vi.fn(), importBlueprintAsync: vi.fn(),
    listBlueprints: vi.fn(),
  },
}));

import toast from 'react-hot-toast';
import store from '@app/store';
import { setSelectedProject } from '@features/dashboard/dashboardSlice';
import { blueprintService } from '../services/blueprintService';
import {
  importBlueprintThunk, importBlueprintAsyncThunk, pollOutlineImportJobThunk,
} from '../blueprintThunks';
import { jobTypeMeta } from '@features/jobs/jobLabels';
import { applyTerminology, labelsFromState } from '@config/tenantLabels';

// CAS-167: the Blueprint page's import messages hardcoded "Outline", which is
// only right on AIM (Blueprint renamed to "Outline"). Default tenants call the
// page "Blueprint" and saw "Outline" anyway.
const AIM_LABELS = { title: 'Block', cdd: 'Blueprint', blueprint: 'Outline' };
const asTenant = (uiLabels) => store.dispatch(setSelectedProject({ id: 1, name: 't', ui_labels: uiLabels }));

describe('Blueprint import success toast as shown (CAS-167)', () => {
  beforeEach(() => {
    shown.success.mockReset();
    blueprintService.getJobStatus.mockReset();
    blueprintService.getJobStatus.mockResolvedValue({ status: 'completed' });
    blueprintService.importBlueprint.mockReset();
    blueprintService.importBlueprint.mockResolvedValue({ id: 1 });
  });

  it('says Blueprint, not Outline, for a tenant with default labels', async () => {
    asTenant({});
    await store.dispatch(pollOutlineImportJobThunk({ jobId: 'job-1' }));
    const message = shown.success.mock.calls[0][0];
    expect(message).toBe('Blueprint imported and set as active.');
    expect(message).not.toMatch(/outline/i);
  });

  it("uses a tenant's renamed label, and still says Outline on AIM", async () => {
    asTenant({ blueprint: 'Learning' });
    await store.dispatch(pollOutlineImportJobThunk({ jobId: 'job-1' }));
    expect(shown.success.mock.calls[0][0]).toBe('Learning imported and set as active.');
    asTenant(AIM_LABELS);
    await store.dispatch(pollOutlineImportJobThunk({ jobId: 'job-2' }));
    expect(shown.success.mock.calls[1][0]).toBe('Outline imported and set as active.');
  });

  it('the direct (non-job) import toast says Blueprint too', async () => {
    asTenant({});
    await store.dispatch(importBlueprintThunk({ projectId: 1 }));
    const message = shown.success.mock.calls[0][0];
    expect(message).toBe('Blueprint imported and set as active.');
    expect(message).not.toMatch(/outline/i);
  });
});

describe('Blueprint import "no project" error (CAS-167)', () => {
  // Rendered as page text (BlueprintPage ErrorState), not a toast.
  it.each([
    ['async', importBlueprintAsyncThunk],
    ['direct', importBlueprintThunk],
  ])('%s import names the Blueprint label with the right article', async (_, thunk) => {
    asTenant({});
    const plain = await store.dispatch(thunk({}));
    expect(plain.payload).toBe('Select a project before importing a Blueprint.');
    expect(plain.payload).not.toMatch(/outline/i);
    asTenant(AIM_LABELS);
    const aim = await store.dispatch(thunk({}));
    expect(aim.payload).toBe('Select a project before importing an Outline.');
  });
});

describe('job tracker import texts as shown (CAS-167)', () => {
  // Same path as jobsThunks.notifyIfNeeded: translate once, then the toast wrapper.
  const trackerToast = (text) => {
    shown.success.mockReset();
    toast.success(applyTerminology(text, labelsFromState(store.getState)));
    return shown.success.mock.calls[0][0];
  };
  const bellTitle = (text) => applyTerminology(text, labelsFromState(store.getState));
  const meta = jobTypeMeta('outline_import');

  it('say Blueprint, not Outline, for a tenant with default labels', () => {
    asTenant({});
    expect(trackerToast(meta.complete)).toBe('Blueprint import complete');
    expect(trackerToast(meta.failed)).toBe('Blueprint import failed');
    expect(bellTitle(meta.label)).toBe('Blueprint import');
    [meta.label, meta.complete, meta.failed].forEach((t) => expect(t).not.toMatch(/outline/i));
  });

  it('still say Outline on AIM', () => {
    asTenant(AIM_LABELS);
    expect(trackerToast(meta.complete)).toBe('Outline import complete');
    expect(trackerToast(meta.failed)).toBe('Outline import failed');
    expect(bellTitle(meta.label)).toBe('Outline import');
  });
});
