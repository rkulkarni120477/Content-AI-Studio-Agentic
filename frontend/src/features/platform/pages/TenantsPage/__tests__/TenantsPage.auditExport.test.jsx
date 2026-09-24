// @vitest-environment jsdom
/** CAS-136 review: a failed audit-log export must never surface a raw
 * API/HTTP error (a FastAPI validation message, or axios's generic
 * "Request failed with status code ..." for a non-JSON error body) — always
 * a friendly, actionable message instead. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import toast from 'react-hot-toast';

const listTenants = vi.fn();
const getAuditTrail = vi.fn();
const getAuditTrailFilters = vi.fn();
const exportAudit = vi.fn();

vi.mock('@features/platform/services/platformService', () => ({
  platformService: {
    listTenants: (...a) => listTenants(...a),
    createTenant: vi.fn(),
    updateTenant: vi.fn(),
    deleteTenant: vi.fn(),
  },
}));

vi.mock('@features/analytics/services/analyticsService', () => ({
  analyticsService: {
    getAuditTrail: (...a) => getAuditTrail(...a),
    getAuditTrailFilters: (...a) => getAuditTrailFilters(...a),
    exportAudit: (...a) => exportAudit(...a),
  },
}));

vi.mock('@hooks/useAuth', () => ({
  useAuth: () => ({
    logout: vi.fn(),
    user: { username: 'platform' },
    role: 'admin',
    isPlatformAdmin: true,
  }),
}));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

vi.mock('@components/common/AppBrand/AppBrand', () => ({ default: () => <span /> }));
vi.mock('@components/common/HeaderUser/IdentityBar', () => ({ default: () => null }));
vi.mock('@components/streamlit/SelectionPageHeader/SelectionPageHeader', () => ({
  default: ({ children }) => <div>{children}</div>,
}));
vi.mock('@components/streamlit/SectionBadge/SectionBadge', () => ({ default: () => null }));
vi.mock('@features/platform/components/TenantLabelsPanel/TenantLabelsPanel', () => ({ default: () => null }));
vi.mock('@features/analytics/pages/AnalyticsPage/AnalyticsPage', () => ({ default: () => null }));

const { default: TenantsPage } = await import('../TenantsPage');

async function openAuditLogTab() {
  render(
    <MemoryRouter>
      <TenantsPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(listTenants).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: /Audit Log/i }));
  await waitFor(() => expect(getAuditTrail).toHaveBeenCalled());
}

beforeEach(() => {
  listTenants.mockResolvedValue([]);
  getAuditTrail.mockResolvedValue({ items: [], total: 0 });
  getAuditTrailFilters.mockResolvedValue({ actors: [], actions: [], entity_types: [], projects: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('TenantsPage — audit export failure message', () => {
  it('shows a friendly message for a FastAPI validation error, not the raw detail', async () => {
    exportAudit.mockRejectedValue({
      response: { status: 422, data: { detail: [{ loc: ['query', 'page_size'], msg: 'Input should be less than or equal to 200' }] } },
    });

    await openAuditLogTab();
    fireEvent.click(screen.getByRole('button', { name: /Export to CSV/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Unable to export audit logs. Please try again.'));
    expect(toast.error).not.toHaveBeenCalledWith(expect.stringContaining('page_size'));
  });

  it('shows a friendly message for a non-JSON error body (e.g. a 502 HTML page), not the generic axios message', async () => {
    exportAudit.mockRejectedValue(new Error('Request failed with status code 502'));

    await openAuditLogTab();
    fireEvent.click(screen.getByRole('button', { name: /Export to CSV/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Unable to export audit logs. Please try again.'));
    expect(toast.error).not.toHaveBeenCalledWith(expect.stringContaining('status code'));
  });
});
