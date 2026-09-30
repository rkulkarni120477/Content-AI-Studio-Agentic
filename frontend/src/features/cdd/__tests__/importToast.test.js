// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../services/cddService', () => ({
  cddService: { importCdd: vi.fn() },
}));
vi.mock('@utils/deferredToast', () => ({ queueDeferredToast: vi.fn() }));

import toast from 'react-hot-toast';
import { cddService } from '../services/cddService';
import { queueDeferredToast } from '@utils/deferredToast';
import { importCddThunk } from '../cddThunks';

// CAS-156: AIM renames CDD → "Blueprint" and Blueprint → "Outline", so a CDD
// import toasting with the Blueprint label read "Outline imported…" on AIM.
const AIM_LABELS = { title: 'Block', cdd: 'Blueprint', blueprint: 'Outline' };

const run = (uiLabels) => importCddThunk({ projectId: 23 })(
  vi.fn(),
  () => ({ dashboard: { selectedProject: { id: 23, ui_labels: uiLabels } } }),
  undefined,
);

describe('CDD import success toast (CAS-156)', () => {
  beforeEach(() => {
    toast.success.mockReset();
    queueDeferredToast.mockReset();
    cddService.importCdd.mockReset();
    cddService.importCdd.mockResolvedValue({ id: 1 });
  });

  it("names the CDD page's own label on AIM, not Outline", async () => {
    await run(AIM_LABELS);
    const message = toast.success.mock.calls[0][0];
    expect(message).toBe('Blueprint imported and set as active.');
    expect(message).not.toMatch(/outline/i);
    const deferred = queueDeferredToast.mock.calls[0][0];
    expect(deferred).toBe('Blueprint imported and pinned as active.');
    expect(deferred).not.toMatch(/outline/i);
  });

  it('says CDD for a tenant without renamed labels', async () => {
    await run(undefined);
    expect(toast.success.mock.calls[0][0]).toBe('CDD imported and set as active.');
    expect(queueDeferredToast.mock.calls[0][0]).toBe('CDD imported and pinned as active.');
  });
});
