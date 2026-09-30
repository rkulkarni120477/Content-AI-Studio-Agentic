// @vitest-environment jsdom
//
// Tenant-isolation ticket: a pipeline prompt a platform admin creates through
// the Prompt Library console (this form) used to always land shared/global
// (project_id null, visible to every tenant) — createPipelinePrompt never
// sent project_id at all, even though the backend already supports stamping
// one (PromptCreateRequest.project_id). Confirmed live: "AIM Block Blueprint"
// (owner: platformadmin, project_id: null in the DB) appeared selected in the
// CDD prompt dropdown for both the unrelated "QA-TEST" and "Cengage" tenants.
// Fixed by adding a platform-admin-only "Tenant" picker on create and wiring
// its value through as project_id.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const createPipelinePrompt = vi.fn(() => Promise.resolve({ id: 99 }));
const listTenants = vi.fn(() => Promise.resolve([{ id: 23, name: 'AIM', slug: 'aim' }]));
let authUser = { username: 'admin', role: 'admin', is_platform_admin: true };

vi.mock('../../api/prompts', () => ({
  fetchPrompt: () => Promise.resolve(null),
  checkDuplicate: () => Promise.resolve(null),
  deleteAttachment: () => Promise.resolve(),
  updatePrompt: () => Promise.resolve({}),
  uploadAttachment: () => Promise.resolve({}),
}));
vi.mock('../../api/pipeline', () => ({
  createPipelinePrompt: (...args) => createPipelinePrompt(...args),
  commitPipelineVersion: () => Promise.resolve({}),
  setPipelineVariables: () => Promise.resolve({}),
  updatePipelineMeta: () => Promise.resolve({}),
}));
vi.mock('../../api/teams', () => ({ fetchTeams: () => Promise.resolve([]) }));
vi.mock('../../context/ToastContext', () => ({ useToast: () => ({ show: vi.fn() }) }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: authUser }) }));
vi.mock('@hooks/useLabels', () => ({ useLabels: () => ({}) }));
vi.mock('@features/platform/services/platformService', () => ({
  platformService: { listTenants: (...args) => listTenants(...args) },
}));

const { default: PromptFormPage } = await import('../PromptFormPage');

function renderPage() {
  return render(
    <MemoryRouter>
      <PromptFormPage />
    </MemoryRouter>,
  );
}

// This form's fields are plain <label> + input siblings with no htmlFor/id
// association, so getByLabelText can't resolve them — walk to the label's
// next sibling instead.
function fieldFor(labelText, options) {
  return screen.getByText(labelText, options).nextElementSibling;
}

async function fillAndSubmit() {
  fireEvent.change(screen.getByPlaceholderText('unique_slug_generation'), { target: { value: 'my_prompt' } });
  fireEvent.change(fieldFor('System prompt', { exact: false }), { target: { value: 'sys' } });
  fireEvent.change(fieldFor('User prompt template', { exact: false }), { target: { value: 'user' } });
  fireEvent.click(screen.getByText('Create prompt'));
  await waitFor(() => expect(createPipelinePrompt).toHaveBeenCalled());
}

afterEach(() => {
  cleanup();
  createPipelinePrompt.mockClear();
  listTenants.mockClear();
});

describe('PromptFormPage — tenant scope on create', () => {
  it('stamps the selected tenant as project_id for a platform admin', async () => {
    authUser = { username: 'admin', role: 'admin', is_platform_admin: true };
    renderPage();
    await waitFor(() => expect(screen.getByText('AIM (aim)')).toBeTruthy());

    fireEvent.change(fieldFor('Tenant (optional)'), { target: { value: '23' } });
    await fillAndSubmit();

    expect(createPipelinePrompt.mock.calls[0][0]).toMatchObject({ projectId: 23 });
  });

  it('defaults to shared/global (null) when no tenant is picked', async () => {
    authUser = { username: 'admin', role: 'admin', is_platform_admin: true };
    renderPage();
    await waitFor(() => expect(screen.getByText('Tenant (optional)')).toBeTruthy());

    await fillAndSubmit();

    expect(createPipelinePrompt.mock.calls[0][0]).toMatchObject({ projectId: null });
  });

  it('hides the tenant picker for a non-platform-admin (their own tenant is already enforced server-side)', async () => {
    authUser = { username: 'tenant-admin', role: 'admin', is_platform_admin: false };
    renderPage();

    await fillAndSubmit();

    expect(screen.queryByText('Tenant (optional)')).toBeNull();
    expect(listTenants).not.toHaveBeenCalled();
    expect(createPipelinePrompt.mock.calls[0][0]).toMatchObject({ projectId: null });
  });
});
