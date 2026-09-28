// @vitest-environment jsdom
/** CAS-140: the audit trail list no longer carries metadata_json (that's
 * what made the page/filtered-search slow) -- expanding a row must lazily
 * fetch the full event via GET /audit-trail/{id} instead of just reading
 * ev.metadata off the already-loaded list item. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listTenants = vi.fn();
const getAuditTrail = vi.fn();
const getAuditTrailFilters = vi.fn();
const getAuditEvent = vi.fn();

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
    getAuditEvent: (...a) => getAuditEvent(...a),
    exportAudit: vi.fn(),
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

function renderPage() {
  return render(
    <MemoryRouter>
      <TenantsPage />
    </MemoryRouter>,
  );
}

const LIST_ROW = {
  id: 42,
  actor: 'alice',
  action: 'content.generated',
  label: 'Content generated',
  icon: '⚡',
  project_id: null,
  created_at: '2026-09-20T10:00:00Z',
  metadata: null, // the list endpoint never sends this any more
};

beforeEach(() => {
  listTenants.mockResolvedValue([]);
  getAuditTrail.mockResolvedValue({ items: [LIST_ROW], total: 1 });
  getAuditTrailFilters.mockResolvedValue({ actors: [], actions: [], entity_types: [], projects: [] });
  getAuditEvent.mockResolvedValue({
    ...LIST_ROW,
    metadata: { block_type: 'lesson', output: 'the real generated content' },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('TenantsPage — audit trail row detail', () => {
  it('does not fetch event detail until a row is expanded', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Audit Log/i }));
    await waitFor(() => expect(getAuditTrail).toHaveBeenCalled());
    await screen.findByText(/Content generated/);

    expect(getAuditEvent).not.toHaveBeenCalled();
  });

  it('fetches and renders the full metadata on expand', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Audit Log/i }));
    await waitFor(() => expect(getAuditTrail).toHaveBeenCalled());
    const row = await screen.findByText(/Content generated/);

    fireEvent.click(row.closest('tr'));

    await waitFor(() => expect(getAuditEvent).toHaveBeenCalledWith(42));
    await screen.findByText('lesson');
    expect(screen.getByRole('button', { name: /View Content/i })).toBeTruthy();
  });

  it('only fetches an event once, even if collapsed and re-expanded', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Audit Log/i }));
    await waitFor(() => expect(getAuditTrail).toHaveBeenCalled());
    const row = await screen.findByText(/Content generated/);

    fireEvent.click(row.closest('tr'));
    await waitFor(() => expect(getAuditEvent).toHaveBeenCalledTimes(1));
    fireEvent.click(row.closest('tr')); // collapse
    fireEvent.click(row.closest('tr')); // re-expand

    await screen.findByText('lesson');
    expect(getAuditEvent).toHaveBeenCalledTimes(1);
  });
});
