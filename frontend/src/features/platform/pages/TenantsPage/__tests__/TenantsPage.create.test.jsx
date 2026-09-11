// @vitest-environment jsdom
/** Phase 8A: New Tenant form exposes template selection in createTenant payload. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listTenants = vi.fn();
const createTenant = vi.fn();

vi.mock('@features/platform/services/platformService', () => ({
  platformService: {
    listTenants: (...a) => listTenants(...a),
    createTenant: (...a) => createTenant(...a),
    updateTenant: vi.fn(),
    deleteTenant: vi.fn(),
  },
}));

vi.mock('@features/analytics/services/analyticsService', () => ({
  analyticsService: {
    getAuditTrail: vi.fn(),
    getAuditTrailFilters: vi.fn(),
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

async function openNewTenantForm() {
  renderPage();
  await waitFor(() => expect(listTenants).toHaveBeenCalled());
  fireEvent.click(screen.getAllByRole('button', { name: '+ New Tenant' })[0]);
  await screen.findByLabelText(/Template/i);
}

async function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText(/Organization code/i), { target: { value: 'acme001' } });
  fireEvent.change(screen.getByLabelText(/Client Name/i), { target: { value: 'Acme Corp' } });
  fireEvent.change(screen.getByLabelText(/^Username/i), { target: { value: 'acme_admin' } });
  fireEvent.change(screen.getByLabelText(/^Password/i), { target: { value: 'secret12' } });
}

beforeEach(() => {
  listTenants.mockResolvedValue([]);
  createTenant.mockResolvedValue({ id: 1, slug: 'acme001', name: 'Acme Corp' });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('TenantsPage — New Tenant template selection', () => {
  it('displays the Template selector with Minimal as default', async () => {
    await openNewTenantForm();
    const select = screen.getByLabelText(/Template/i);
    expect(select).toBeTruthy();
    expect(select.value).toBe('minimal');
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      'Minimal',
      'Academic',
      'Publishing',
    ]);
  });

  it('includes the selected template in createTenant payload', async () => {
    await openNewTenantForm();
    await fillRequiredFields();
    fireEvent.change(screen.getByLabelText(/Template/i), { target: { value: 'academic' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Tenant' }));

    await waitFor(() => expect(createTenant).toHaveBeenCalledTimes(1));
    expect(createTenant.mock.calls[0][0]).toMatchObject({
      slug: 'acme001',
      name: 'Acme Corp',
      client_name: 'Acme Corp',
      max_users: 50,
      admin_username: 'acme_admin',
      admin_password: 'secret12',
      admin_display_name: 'acme_admin',
      template: 'academic',
    });
  });

  it('can select Publishing and sends template in payload', async () => {
    await openNewTenantForm();
    await fillRequiredFields();
    fireEvent.change(screen.getByLabelText(/Template/i), { target: { value: 'publishing' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Tenant' }));

    await waitFor(() => expect(createTenant).toHaveBeenCalledTimes(1));
    expect(createTenant.mock.calls[0][0].template).toBe('publishing');
  });

  it('sends minimal when the selector is left unchanged', async () => {
    await openNewTenantForm();
    await fillRequiredFields();
    fireEvent.click(screen.getByRole('button', { name: 'Create Tenant' }));

    await waitFor(() => expect(createTenant).toHaveBeenCalledTimes(1));
    expect(createTenant.mock.calls[0][0].template).toBe('minimal');
  });
});
